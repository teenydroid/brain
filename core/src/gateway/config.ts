// The OpenClaw config the core generates (OPENCLAW_CONFIG_PATH). Every key
// below was read from the installed package's own schema, `openclaw config
// schema` for openclaw@2026.11.10 (the JSON Schema it validates against), and
// its docs; the `// schema:` notes give the path. The config is written by the
// core, never by hand and never by the agents: the gateway tool that edits
// config is denied to every agent (agents/definitions.ts).
//
// What it locks down:
//  - no `channels` block at all (Telegram stays in Python);
//  - hooks.enabled=false, the chat-completions and responses HTTP endpoints
//    off, the control UI off, update.checkOnStart=false, mDNS off;
//  - plugins limited by allowlist to the browser plugin and Teeny's own local
//    `teeny-guardrails` plugin (when its directory exists);
//  - gateway bound to loopback with a shared token that arrives in the
//    environment (OPENCLAW_GATEWAY_TOKEN), never in this file.
import { existsSync } from "node:fs";

/** The env var the Gateway reads its shared token from (docs/cli/gateway/running.md, --token). */
export const GATEWAY_TOKEN_ENV = "OPENCLAW_GATEWAY_TOKEN";
/** The env var the model endpoint key travels in; the config only names it. */
export const MODEL_KEY_ENV = "LOCAL_OPENAI_API_KEY";
export const PROVIDER_ID = "local-openai";
export const GUARDRAILS_PLUGIN_ID = "teeny-guardrails";

/** Plugins nothing of Teeny's needs; belt and braces over the allowlist. */
const DENIED_PLUGINS = ["telegram", "x", "imap", "a2a", "bonjour", "admin-http-rpc", "session-share", "openclaw-id-access", "device-pair"];

export interface AgentEntry {
  name: string;
  description: string;
  workspace: string;
  model?: string | { primary: string };
  tools: Record<string, unknown>;
  params?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface GatewayConfigInput {
  port: number;
  /** Endpoint of the OpenAI-compatible model server ($LOCAL_OPENAI_URL). */
  modelUrl: string | undefined;
  /** $SPINE_MODEL; the model every workload uses. */
  model: string | undefined;
  /** Every model the endpoint serves that a turn may ask for (the default plus the registry's), with its window when known. */
  models: { id: string; contextWindow?: number }[];
  /** Tokens at which a thread folds (bin/lib/core_sections/context.py: 100000). */
  compactionTriggerTokens: number;
  /** Recent tokens kept verbatim across a fold (context.py: 40000). */
  keepRecentTokens: number;
  agentTimeoutSecs: number;
  defaultWorkspace: string;
  agents: Record<string, AgentEntry>;
  /** core/plugins/teeny-guardrails when it exists (P3 writes it). */
  guardrailsPath?: string;
}

export function guardrailsIfPresent(path: string): string | undefined {
  return existsSync(path) ? path : undefined;
}

export function buildGatewayConfig(input: GatewayConfigInput): Record<string, unknown> {
  const modelRef = input.model ? `${PROVIDER_ID}/${input.model}` : undefined;
  const plugins: Record<string, unknown> = {
    // schema: plugins.allow (non-empty allowlist restricts loading), plugins.deny, plugins.load.paths
    allow: ["browser", ...(input.guardrailsPath ? [GUARDRAILS_PLUGIN_ID] : [])],
    deny: DENIED_PLUGINS,
    ...(input.guardrailsPath
      ? {
          load: { paths: [input.guardrailsPath] },
          entries: { [GUARDRAILS_PLUGIN_ID]: { enabled: true } },
        }
      : {}),
  };

  return {
    // schema: gateway.mode / port / bind / auth.mode. The Gateway refuses to
    // start unless gateway.mode=local (docs/cli/gateway/running.md).
    gateway: {
      mode: "local",
      port: input.port,
      bind: "loopback",
      auth: { mode: "token" },
      controlUi: { enabled: false },
      // schema: gateway.http.endpoints.{chatCompletions,responses}.enabled
      http: { endpoints: { chatCompletions: { enabled: false }, responses: { enabled: false } } },
      // The core edits config; a hot reload on its writes is not wanted.
      reload: { mode: "off" },
    },
    // schema: hooks.enabled
    hooks: { enabled: false },
    // schema: update.checkOnStart
    update: { checkOnStart: false },
    // schema: discovery.mdns.mode
    discovery: { mdns: { mode: "off" } },
    // schema: telemetry.enabled
    telemetry: { enabled: false },
    plugins,
    // schema: models.providers.<id> {baseUrl, api, apiKey (SecretRef env), models[]}
    ...(input.modelUrl && input.model
      ? {
          models: {
            mode: "merge",
            providers: {
              [PROVIDER_ID]: {
                baseUrl: input.modelUrl,
                api: "openai-completions",
                apiKey: { source: "env", provider: "default", id: MODEL_KEY_ENV },
                models: input.models.map((m) => ({
                  id: m.id,
                  name: m.id,
                  // schema: models.providers.*.models[].contextWindow / contextTokens ("session's per-model
                  // cap"): contextTokens folds a thread at the trigger instead of at the window.
                  ...(m.contextWindow ? { contextWindow: m.contextWindow } : {}),
                  contextTokens: m.contextWindow ? Math.min(m.contextWindow, input.compactionTriggerTokens) : input.compactionTriggerTokens,
                })),
              },
            },
          },
        }
      : {}),
    agents: {
      // schema: agents.ownership; `openclaw config validate` demands it of a multi-agent roster.
      ownership: "explicit",
      defaults: {
        ...(modelRef ? { model: { primary: modelRef } } : {}),
        workspace: input.defaultWorkspace,
        // The core writes each workspace's AGENTS.md itself.
        skipBootstrap: true,
        // The built prompts run to tens of thousands of characters.
        bootstrapMaxChars: 400_000,
        bootstrapTotalMaxChars: 800_000,
        timeoutSeconds: input.agentTimeoutSecs,
        // schema: agents.defaults.compaction.{enabled,mode,keepRecentTokens}
        compaction: { enabled: true, mode: "default", keepRecentTokens: input.keepRecentTokens },
      },
      // schema: agents.entries.<id>
      entries: input.agents,
    },
    // No `channels`, no `bindings`, no `cron` here: P2 owns automations.
  };
}
