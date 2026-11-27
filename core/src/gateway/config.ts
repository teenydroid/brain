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
  /**
   * Files that hold the Gateway's two secrets (each a single value, 0600, in a private
   * directory). With them the config names SecretRefs of source "file" and the Gateway
   * process has neither secret in its environment. That matters because OpenClaw's exec
   * tool merges the Gateway's environment into every command an agent runs: a hand
   * started through it would otherwise inherit the Gateway token (which can reconfigure
   * the Gateway and call tools around the guardrails) and the model key. Without them
   * the config falls back to SecretRefs of source "env" (tests, and nothing else).
   */
  secretFiles?: { gatewayToken: string; modelKey: string };
  /**
   * The agent that owns jobs and traffic no agent claims: `agents.defaults.systemAgent.agentId`.
   * OpenClaw refuses to add a shell (command) cron job without an owner, and a multi-agent
   * roster has none by default. Teeny's shell jobs run no model, so this is the tool-less
   * `simple` agent, never one with hands.
   */
  systemAgentId?: string;
  /** core/plugins/teeny-guardrails when it exists (P3 writes it). */
  guardrailsPath?: string;
  /**
   * True once teeny-guardrails has shown it loaded in the running Gateway. Until then a shell agent's exec is
   * "deny" even with the plugin in the config, so a Gateway whose plugin failed to load (and whose own cron
   * can start agent turns the moment it is up) has no exec to abuse. Default: false.
   */
  guardrailsProven?: boolean;
  /** The policy the plugin enforces (agents/guardrails.ts buildGuardrailsPolicy). */
  guardrailsPolicy?: Record<string, unknown>;
  /** Extra skill directories (`skills.load.extraDirs`): brain/skills/hands, which the agents' workspaces do not contain. */
  skillDirs?: string[];
  /** Chrome for the browser tool; unset lets OpenClaw find one. */
  chromePath?: string;
  /**
   * `logging.file`: the day's log, in a directory the core made private. Left unset OpenClaw resolves
   * a "secure temp root" for its log and refuses every ancestor that is group-writable, then drops
   * every record with "log file append failed". An explicit file skips that resolution.
   */
  logFile?: string;
}

/** The two browser profiles the guardrails plugin pins agents to: Teeny's own (signed in) and a throwaway for readers. */
export const BROWSER_PROFILES = { operator: "teeny", view: "view" } as const;

export const SECRET_PROVIDER_TOKEN = "teeny_gateway_token";
export const SECRET_PROVIDER_MODEL_KEY = "teeny_model_key";
/** A SecretRef into a `singleValue` file provider (docs/gateway/config-secrets-env.md). */
const fileRef = (provider: string): { source: "file"; provider: string; id: "value" } => ({ source: "file", provider, id: "value" });

/**
 * The agents' entries as written to the config. A shell agent's exec runs with OpenClaw's own check
 * off because teeny-guardrails is the gate (agents/definitions.ts); with no plugin in the config, or one
 * not yet proven loaded, there is no gate, so exec is "deny" and a shell agent can run nothing.
 */
export function guardedEntries(agents: Record<string, AgentEntry>, guarded: boolean): Record<string, AgentEntry> {
  if (guarded) return agents;
  const out: Record<string, AgentEntry> = {};
  for (const [id, entry] of Object.entries(agents)) {
    const exec = entry.tools.exec;
    out[id] = exec && typeof exec === "object" ? { ...entry, tools: { ...entry.tools, exec: { ...(exec as Record<string, unknown>), security: "deny" } } } : entry;
  }
  return out;
}

export function guardrailsIfPresent(path: string): string | undefined {
  return existsSync(path) ? path : undefined;
}

export function buildGatewayConfig(input: GatewayConfigInput): Record<string, unknown> {
  const modelRef = input.model ? `${PROVIDER_ID}/${input.model}` : undefined;
  const plugins: Record<string, unknown> = {
    // schema: plugins.slots.memory "none" turns every memory plugin off (docs/tools/plugin.md). memory-core
    // would otherwise create its own agent cron job, "Memory Dreaming Promotion" (0 3 * * *), outside
    // brain/cron.toml; Teeny's memory is CortexDB.
    slots: { memory: "none" },
    // schema: plugins.allow (non-empty allowlist restricts loading), plugins.deny, plugins.load.paths
    allow: ["browser", ...(input.guardrailsPath ? [GUARDRAILS_PLUGIN_ID] : [])],
    deny: DENIED_PLUGINS,
    ...(input.guardrailsPath
      ? {
          load: { paths: [input.guardrailsPath] },
        }
      : {}),
    entries: {
      // schema: plugins.entries.<id>.enabled; plugins.entries.memory-core.config.dreaming.enabled
      // (docs/reference/memory-config.md): belt and braces over the slot.
      "memory-core": { enabled: false, config: { dreaming: { enabled: false } } },
      ...(input.guardrailsPath
        ? {
            [GUARDRAILS_PLUGIN_ID]: {
              enabled: true,
              // before_prompt_build (memory recall) reads the conversation, which a non-bundled plugin may only
              // do with this flag (docs/plugins/hooks.md; `openclaw plugins inspect` diagnostics).
              hooks: { allowConversationAccess: true },
              // schema: plugins.entries.<id>.config (validated by the plugin's own configSchema)
              config: { policy: input.guardrailsPolicy ?? {} },
            },
          }
        : {}),
    },
  };

  return {
    // schema: gateway.mode / port / bind / auth.mode. The Gateway refuses to
    // start unless gateway.mode=local (docs/cli/gateway/running.md).
    gateway: {
      mode: "local",
      port: input.port,
      bind: "loopback",
      // schema: gateway.auth.token is a SecretRef surface (reference/secretref-credential-surface.md).
      auth: { mode: "token", ...(input.secretFiles ? { token: fileRef(SECRET_PROVIDER_TOKEN) } : {}) },
      controlUi: { enabled: false },
      // schema: gateway.http.endpoints.{chatCompletions,responses}.enabled
      http: { endpoints: { chatCompletions: { enabled: false }, responses: { enabled: false } } },
      // schema: gateway.reload.mode (gateway/configuration/hot-reload.md: "off" | "hybrid"). "hybrid", so the
      // running Gateway applies the core's one live write, exec opening once teeny-guardrails is proven loaded.
      // With "off" that write never applied and every shell agent stayed on "deny". The core writes exec open
      // only when nothing else changed (main.ts writeConfig), so a restart the Gateway makes itself starts on "deny".
      reload: { mode: "hybrid" },
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
    ...(input.secretFiles
      ? {
          // schema: secrets.providers.<id> {source: "file", path, mode: "singleValue"}
          secrets: {
            providers: {
              [SECRET_PROVIDER_TOKEN]: { source: "file", path: input.secretFiles.gatewayToken, mode: "singleValue" },
              [SECRET_PROVIDER_MODEL_KEY]: { source: "file", path: input.secretFiles.modelKey, mode: "singleValue" },
            },
          },
        }
      : {}),
    // schema: browser.{enabled,headless,defaultProfile,executablePath,profiles.<name>.cdpPort}
    // (docs/tools/browser/configuration.md). The plugin pins each agent to one of these profiles.
    // The user-data dir of a managed profile cannot be set: `userDataDir` exists only for `existing-session`
    // profiles (configuration.md, "Set browser.profiles.<name>.userDataDir when an existing-session profile
    // should attach to a non-default Chromium user profile"), which attach to a running Chrome and need an
    // approval prompt. A managed profile keeps its cookies in $OPENCLAW_STATE_DIR/browser/<profile>/user-data
    // (docs/tools/browser-linux-troubleshooting.md), which is state/openclaw/browser/teeny/user-data here and
    // persists across restarts. P5 MIGRATION: with the browser stopped, copy state/browser/profile/ to
    // state/openclaw/browser/teeny/user-data/ (mode 0700, same owner) so the sign-ins OpenHuman kept carry over.
    browser: {
      enabled: true,
      headless: true,
      defaultProfile: BROWSER_PROFILES.operator,
      ...(input.chromePath ? { executablePath: input.chromePath } : {}),
      profiles: { [BROWSER_PROFILES.operator]: { cdpPort: 18800 }, [BROWSER_PROFILES.view]: { cdpPort: 18801 } },
    },
    // schema: models.providers.<id> {baseUrl, api, apiKey (SecretRef env), models[]}
    ...(input.modelUrl && input.model
      ? {
          models: {
            mode: "merge",
            providers: {
              [PROVIDER_ID]: {
                baseUrl: input.modelUrl,
                api: "openai-completions",
                apiKey: input.secretFiles ? fileRef(SECRET_PROVIDER_MODEL_KEY) : { source: "env", provider: "default", id: MODEL_KEY_ENV },
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
        // schema: agents.defaults.systemAgent.agentId (docs/concepts/multi-agent.md)
        ...(input.systemAgentId ? { systemAgent: { agentId: input.systemAgentId } } : {}),
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
      entries: guardedEntries(input.agents, input.guardrailsPath !== undefined && input.guardrailsProven === true),
    },
    ...(input.skillDirs && input.skillDirs.length > 0 ? { skills: { load: { extraDirs: input.skillDirs } } } : {}),
    // schema: cron.skipMissedJobs (docs/gateway/config-automation.md, docs/automation/cron-jobs/managing-jobs.md):
    // recurring slots missed while the Gateway was down advance to the next
    // future one instead of replaying; late runs belong to bin/lib/catch_up.py.
    // The cron module sets the same key through the CLI at boot; the file says it first.
    cron: { skipMissedJobs: true },
    // schema: logging.file (docs/gateway/logging.md): the rolling name `openclaw-YYYY-MM-DD.log` is dated per day.
    ...(input.logFile ? { logging: { file: input.logFile } } : {}),
    // No `channels`, no `bindings` here.
  };
}
