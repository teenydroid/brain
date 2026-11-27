// cron_list, cron_add, cron_update, cron_remove, cron_run over OpenClaw automations: see README.md.
import type { CoreContext, RpcMethods } from "../server/types.js";
import { createCron } from "./scheduler.js";

export { createCron, type Cron, type CronOptions } from "./scheduler.js";
export { commandOf, shellArgv, view } from "./map.js";

const shared = createCron();

export const methods: RpcMethods = shared.methods;

/**
 * The server calls this once with main.ts's gatewayEnv(): the environment (OPENCLAW_GATEWAY_TOKEN,
 * OPENCLAW_STATE_DIR, OPENCLAW_CONFIG_PATH, the Gateway URL/port) the `openclaw` CLI needs. Until it
 * is called every cron_* RPC fails closed. It is passed to the CLI only, never to a shell job.
 */
export function setGatewayEnv(fn: (() => Record<string, string | undefined>) | undefined): void {
  shared.setGatewayEnv(fn);
}

/** After setGatewayEnv, once the Gateway is up: verifies cron.skipMissedJobs (never sets it). */
export function startCron(ctx: CoreContext): Promise<void> {
  return shared.start(ctx);
}

export function stopCron(): Promise<void> {
  return shared.stop();
}
