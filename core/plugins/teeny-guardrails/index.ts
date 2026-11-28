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
// Memory recall is not here: it is the teeny-cortexdb plugin's (plugins.slots.memory), so it
// happens once. The memory tools it registers are judged here like every other tool.
// A policy that fails to parse blocks every tool call: the plugin refuses when
// it cannot tell what it should allow.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { maskSecrets, picturePaths, savePicture } from "./browser.js";
import { Guardrails } from "./guard.js";
import { parsePolicy } from "./policy.js";
import { wakeContext } from "./wake.js";

export const PLUGIN_ID = "teeny-guardrails";

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "Teeny guardrails",
  description: "Teeny's fences on every tool call: workspace jail, exec allowlist, browser deny list, keychain fill, screenshot sink, tool-call budget and the memory tools' gate.",
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
      (event, ctx) => {
        const tool = event.toolName.toLowerCase();
        // A native playbook's hand that refused (exit 2) gives its day's reservation back (native.ts). Observation
        // only: the count and the lock are taken in before_tool_call, and a lost hook leaves the count where it was.
        if (tool === "exec") {
          const runId = event.runId ?? ctx.runId;
          guard?.observe({ toolName: event.toolName, params: (event.params ?? {}) as Record<string, unknown>, result: event.result }, { ...(ctx.agentId ? { agentId: ctx.agentId } : {}), ...(ctx.sessionKey ? { sessionKey: ctx.sessionKey } : {}), ...(runId ? { runId } : {}) });
          return;
        }
        const policy = guard?.policy;
        if (!policy?.picturesDir || tool !== "browser") return;
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
      { matcher: ["browser", "exec"] },
    );

    api.on("before_prompt_build", async (_event, ctx) => {
      const queue = await wakeContext(guard?.policy.wakeContext, ctx.agentId);
      return queue ? { prependContext: queue } : undefined;
    });

    // The run is over: its lock is released and its record written (native.ts).
    api.on("agent_end", (event, ctx) => {
      guard?.runEnded(event, { ...(ctx.agentId ? { agentId: ctx.agentId } : {}), ...(ctx.sessionKey ? { sessionKey: ctx.sessionKey } : {}), ...(ctx.runId ? { runId: ctx.runId } : {}) });
    });

    // Last: the hooks above are registered. The core's supervisor stops a Gateway that has not written this
    // (an agent's exec runs with OpenClaw's own check off, so a Gateway without the gate must not serve).
    // Not written when the policy is unusable: that Gateway blocks every call, and says so above.
    // Only in the Gateway's own "full" load (docs/plugins/sdk-entrypoints/registration-mode.md): the CLI
    // (cron_list, seed-cron, security audit) loads plugins in other modes and must not vouch for a Gateway.
    // The pid lets the supervisor check that the file is its own child's.
    const ready = api.registrationMode === "full" ? guard?.policy.readyFile : undefined;
    if (ready) {
      try {
        mkdirSync(dirname(ready), { recursive: true, mode: 0o700 });
        writeFileSync(ready, `${JSON.stringify({ pid: process.pid, at: new Date().toISOString() })}\n`, { mode: 0o600 });
      } catch (e) {
        api.logger.error(`teeny-guardrails: could not write ${ready}: ${(e as Error).message}`);
      }
    }
  },
});
