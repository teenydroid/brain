// teeny-cortexdb: Teeny's memory on OpenClaw's memory slot, as one local plugin.
//
// Ported from @cortexdb/openclaw 0.1.4 (Apache-2.0, CortexDB Team; see NOTICE) to the
// plugin API of openclaw@2026.11.10, which removed what that package calls:
//   registerMemoryProvider -> api.registerMemoryCapability (docs/plugins/sdk-overview/memory-and-context.md),
//                             and the manifest says kind "memory" (plugins.slots.memory selects it)
//   ctx.on("after_turn")   -> api.on("agent_end")  (docs/plugins/hooks/prompt-and-session.md)
//   buildCtx.appendSystem  -> before_prompt_build returning appendSystemContext
//   ctx.registerTool       -> api.registerTool, declared in the manifest's contracts.tools
//
//   before_prompt_build    recall into the system context, within the agent's token budget
//   agent_end              capture the turn, for the allowlisted agents, off the turn's path
//   memory_recall/list/store/forget   for the agents the core gave them (the mind)
//
// It fails closed and quietly: a config it cannot read registers nothing, and CortexDB being
// down means a turn runs without memory, never that it fails. The core leaves the plugin out of
// the Gateway's config altogether when CORTEX_API_URL is not set (src/memory/plugin.ts).
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { Capturer } from "./capture.js";
import { clientSource } from "./client.js";
import { parseConfig, type CortexPluginConfig } from "./config.js";
import { recallContext } from "./recall.js";
import { MemoryTools, TOOL_NAMES, TOOL_SPECS, type ToolName } from "./tools.js";

export const PLUGIN_ID = "teeny-cortexdb";

/** What tests may replace: the HTTP the client uses. */
export const testing: { fetch: typeof fetch | undefined } = { fetch: undefined };

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Teeny CortexDB memory",
  description: "Teeny's long-term memory on OpenClaw's memory slot: CortexDB recall into each turn, capture after a turn, and the memory tools.",
  register(api) {
    let config: CortexPluginConfig;
    try {
      config = parseConfig(api.pluginConfig);
    } catch (e) {
      api.logger.error(`${(e as Error).message}; no memory this run`);
      return;
    }
    const source = clientSource(config, testing.fetch);
    const capturer = new Capturer(config, source, (m) => api.logger.warn(m));
    const tools = new MemoryTools(config, source);

    api.registerMemoryCapability({
      // A line of guidance for an agent that was given the tools; none for the rest.
      promptBuilder: ({ availableTools }) =>
        availableTools.has("memory_recall")
          ? [
              "## Memory",
              "Your long-term memory is CortexDB. What bears on a turn is recalled into your context for you. memory_recall looks something up on purpose, memory_store keeps one fact, memory_forget retracts a memory that is wrong (it needs a reason and deletes at most a few).",
              "",
            ]
          : [],
    });

    api.on("before_prompt_build", async (event, ctx) => {
      try {
        const message = (event as { currentUserMessage?: string }).currentUserMessage ?? (event as { prompt?: string }).prompt ?? "";
        const text = await recallContext(config, source, ctx.agentId, message);
        return text ? { appendSystemContext: text } : undefined;
      } catch {
        return undefined; // recall is context: it never fails a turn
      }
    });

    api.on("agent_end", (event, ctx) => {
      try {
        // Detached: start() only queues the write and returns.
        capturer.start(ctx.agentId, event.messages ?? [], event.success !== false);
      } catch (e) {
        api.logger.warn(`${PLUGIN_ID}: capture skipped: ${(e as Error).message}`);
      }
      return undefined;
    });

    api.registerTool(
      (ctx) => {
        const agentId = (ctx as { agentId?: string }).agentId;
        if (!agentId || !config.tools.agents.includes(agentId)) return null;
        return TOOL_NAMES.map((name: ToolName) => ({
          name,
          label: TOOL_SPECS[name].label,
          description: TOOL_SPECS[name].description,
          parameters: TOOL_SPECS[name].parameters,
          async execute(_id: string, params: Record<string, unknown>) {
            const r = await tools.run(name, params ?? {});
            return { content: [{ type: "text" as const, text: r.text }], details: r.details };
          },
        })) as never;
      },
      { names: [...TOOL_NAMES], optional: true },
    );
  },
});
