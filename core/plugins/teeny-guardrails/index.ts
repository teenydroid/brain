// teeny-guardrails: Teeny's fences on the OpenClaw Gateway, as one local plugin.
//
// Hooks used (docs/plugins/hooks/tool-policy.md, docs/plugins/hooks/reference.md,
// docs/plugins/hooks/prompt-and-session.md in the pinned openclaw package):
//   before_tool_call      block / rewrite `params` of every tool call. A thrown
//                         error or a timeout blocks the call (reference.md,
//                         "On thrown error or timeout: Block the run, tool call").
//   tool_result_persist   synchronous; masks a typed keychain password.
//   after_tool_call       observation only; feeds the screenshot sink. It is
//                         never used to enforce anything.
//   before_prompt_build   returns `prependContext`: recalled memory.
// A policy that fails to parse blocks every tool call: the plugin refuses when
// it cannot tell what it should allow.
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { maskSecrets, picturePaths, savePicture } from "./browser.js";
import { Guardrails } from "./guard.js";
import { parsePolicy } from "./policy.js";
import { recallContext } from "./recall.js";

export const PLUGIN_ID = "teeny-guardrails";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Teeny guardrails",
  description: "Teeny's fences on every tool call: workspace jail, exec allowlist, browser deny list, keychain fill, screenshot sink, tool-call budget and memory recall.",
  register(api) {
    let guard: Guardrails | undefined;
    let problem: string | undefined;
    try {
      guard = new Guardrails(parsePolicy(api.pluginConfig));
    } catch (e) {
      problem = (e as Error).message;
      api.logger.error(`teeny-guardrails: ${problem}; every tool call will be blocked`);
    }

    // The highest priority runs first (hooks/reference.md): nothing may rewrite a call before the guard has judged it.
    api.on(
      "before_tool_call",
      async (event, ctx) => {
        if (!guard) return { block: true, blockReason: `teeny-guardrails: no usable policy (${problem})` };
        const decision = await guard.beforeToolCall(
          { toolName: event.toolName, params: (event.params ?? {}) as Record<string, unknown>, ...(event.runId ? { runId: event.runId } : {}) },
          { ...(ctx.agentId ? { agentId: ctx.agentId } : {}), ...(ctx.sessionKey ? { sessionKey: ctx.sessionKey } : {}), ...(ctx.runId ? { runId: ctx.runId } : {}) },
        );
        return decision;
      },
      { priority: 1000 },
    );

    api.on(
      "tool_result_persist",
      (event) => {
        const secrets = guard?.browserState.secrets;
        if (!secrets || secrets.size === 0) return undefined;
        return { message: maskSecrets(event.message, secrets) };
      },
      { priority: 1000 },
    );

    api.on(
      "after_tool_call",
      (event) => {
        const policy = guard?.policy;
        if (!policy?.picturesDir || event.toolName.toLowerCase() !== "browser") return;
        if ((event.params as { action?: unknown } | undefined)?.action !== "screenshot") return;
        // Never keep a picture of a page a password was just typed into.
        if (guard && guard.browserState.secrets.size > 0) return;
        for (const path of picturePaths(event.result)) {
          try {
            savePicture(policy.picturesDir, path, policy.picturesKeep);
          } catch (e) {
            api.logger.warn(`teeny-guardrails: could not keep a browser picture: ${(e as Error).message}`);
          }
        }
      },
      { matcher: ["browser"] },
    );

    api.on("before_prompt_build", async (event, ctx) => {
      const recall = guard?.policy.recall;
      if (!recall) return undefined;
      const message = (event as { currentUserMessage?: string; prompt?: string }).currentUserMessage ?? (event as { prompt?: string }).prompt ?? "";
      const text = await recallContext(recall, ctx.agentId, message);
      return text ? { prependContext: text } : undefined;
    });
  },
});
