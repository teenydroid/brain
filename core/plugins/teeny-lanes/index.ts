// teeny-lanes: which model each OpenClaw session runs on, and what each lane and thread spends.
//
// Hooks used (docs/plugins/hooks/prompt-and-session.md in the pinned openclaw package):
//   before_model_resolve  the session key's lane picks the run's model (and its fallback chain). The override
//                         holds for that run only and is never written to the session.
//   llm_output            observation only: each model call's tokens go to the ledger (lanes.ts Ledger), with
//                         the day's totals and a note the first time a lane or thread passes its cap.
// Nothing here blocks or downgrades a run: caps are logged, not enforced, until the ledger has shown what
// normal looks like. A config that fails to parse leaves every model alone and records nothing.
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Ledger, parseLanes, resolveModel, type LanesConfig } from "./lanes.js";

export const PLUGIN_ID = "teeny-lanes";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Teeny lanes",
  description: "Routes each session to its lane's model and logs the tokens every lane and thread spends against its daily cap.",
  register(api) {
    let cfg: LanesConfig | undefined;
    try {
      cfg = parseLanes(api.pluginConfig);
    } catch (e) {
      api.logger.error(`teeny-lanes: ${(e as Error).message}; models are left alone and nothing is logged`);
    }
    const ledger = cfg ? new Ledger(cfg) : undefined;

    api.on("before_model_resolve", (_event, ctx) => (cfg ? resolveModel(cfg, ctx.sessionKey) : undefined));

    api.on("llm_output", (event, ctx) => {
      if (!ledger) return;
      const session = ctx.sessionKey ?? (ctx.jobId ? `cron:${ctx.jobId}` : `session:${event.sessionId}`);
      try {
        const row = ledger.record({
          ts: Date.now(),
          session,
          ...(ctx.agentId ? { agent: ctx.agentId } : {}),
          model: event.resolvedRef ?? `${event.provider}/${event.model}`,
          ...(event.usage?.input !== undefined ? { input: event.usage.input } : {}),
          ...(event.usage?.output !== undefined ? { output: event.usage.output } : {}),
          ...(event.usage?.cacheRead !== undefined ? { cacheRead: event.usage.cacheRead } : {}),
          ...(event.usage?.total !== undefined ? { total: event.usage.total } : {}),
        });
        for (const what of row.over ?? []) {
          api.logger.warn(
            what === "lane"
              ? `teeny-lanes: lane ${row.lane} is past its daily cap (${row.laneDay} tokens today); logged only`
              : `teeny-lanes: thread ${row.session} (lane ${row.lane}) is past its daily cap (${row.threadDay} tokens today); logged only`,
          );
        }
      } catch (e) {
        api.logger.warn(`teeny-lanes: could not log a call's tokens: ${(e as Error).message}`);
      }
    });
  },
});
