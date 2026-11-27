#!/usr/bin/env node
// teeny-core: the OpenClaw-backed core. `teeny-core serve` answers the
// openhuman.* JSON-RPC on $OPENHUMAN_CORE_HOST:$OPENHUMAN_CORE_PORT and
// supervises the OpenClaw Gateway its agent turns run on.
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { dirname, join } from "node:path";
import { AgentCatalog } from "./agents/provision.js";
import { CostLog } from "./agents/cost.js";
import { bareModel } from "./agents/model.js";
import { resolvePaths, serveSettings } from "./config/paths.js";
import { Sealer, masterKeyFile } from "./config/secrets.js";
import { SettingsStore } from "./config/store.js";
import { resolveToken } from "./config/token.js";
import { CliAgentRunner, type AgentRunner } from "./gateway/client.js";
import { buildGuardrailsPolicy } from "./agents/guardrails.js";
import { findChrome } from "./config/chrome.js";
import { buildGatewayConfig, guardrailsIfPresent } from "./gateway/config.js";
import { ensureGatewayToken, gatewayTokenFile, openclawPassthrough } from "./gateway/passthrough.js";
import { GatewaySupervisor, type SpawnFn } from "./gateway/supervisor.js";
import { parse as parseToml } from "smol-toml";
import { setGatewayEnv, startCron, stopCron } from "./cron/index.js";
import { DrainingRunner } from "./gateway/drain.js";
import { createHttpServer } from "./server/http.js";
import { buildRegistry } from "./server/registry.js";
import type { CoreContext } from "./server/types.js";
import { giveawayTxCli } from "./wallet/cli.js";

const USAGE = "usage: teeny-core serve | giveaway-tx create|close | openclaw ARGS...";
const DEFAULT_SHELL_TIMEOUT_SECS = 1800;
/** bin/lib/core_sections/context.py: COMPACTION_TRIGGER_TOKENS and KEEP_RECENT_TOKENS. */
const COMPACTION_TRIGGER_TOKENS = 100_000;
const KEEP_RECENT_TOKENS = 40_000;
const DEFAULT_GATEWAY_PORT = 18789;
/** How long a Gateway restart waits for running turns (a cron agent turn may run 30 minutes). */
const DRAIN_MS = 15 * 60_000;

type Env = Readonly<Record<string, string | undefined>>;

export interface StartOptions {
  cwd?: string;
  /** Replace the agent runner (tests). */
  runner?: AgentRunner;
  /** Do not start the Gateway child (tests). */
  noGateway?: boolean;
  spawn?: SpawnFn;
  /** How long a settings-driven Gateway restart waits for running turns (tests). */
  drainMs?: number;
  log?: CoreContext["log"];
}

export interface RunningCore {
  host: string;
  port: number;
  stop: () => Promise<void>;
}

function compactionTrigger(env: Env): number {
  const given = env.TEENY_COMPACTION_TRIGGER_TOKENS?.trim();
  if (!given) return COMPACTION_TRIGGER_TOKENS;
  const n = Number(given);
  if (!Number.isInteger(n) || n <= KEEP_RECENT_TOKENS) throw new Error(`TEENY_COMPACTION_TRIGGER_TOKENS must be an integer above ${KEEP_RECENT_TOKENS}`);
  return n;
}

/** Where bin/lib/core_config.py wrote the core's config.toml (the wallet reads it from the same place). */
function coreConfigPath(env: Env, root: string): string {
  const explicit = env.TEENY_CORE_CONFIG?.trim();
  if (explicit) return explicit;
  const workspace = env.OPENHUMAN_WORKSPACE?.trim();
  return join(workspace ? dirname(workspace) : root, ".openhuman", "config.toml");
}

/** `[cron] shell_timeout_secs` from the core's config.toml; the 1800 s bin/lib/core_sections/automation.py writes when absent or unreadable. */
export function cronShellTimeoutSecs(env: Env, root: string): number {
  try {
    const cron = (parseToml(readFileSync(coreConfigPath(env, root), "utf8")) as { cron?: { shell_timeout_secs?: unknown } }).cron;
    const n = Number(cron?.shell_timeout_secs);
    if (Number.isInteger(n) && n > 0) return n;
  } catch {
    // no config yet: the default
  }
  return DEFAULT_SHELL_TIMEOUT_SECS;
}

/**
 * The one place the cron module is hooked up (its RPC methods are in registry.ts). Cron jobs
 * are OpenClaw automations, driven through the `openclaw` CLI, which needs the Gateway's
 * state, config and URL: `cliEnv` is that, handed to the CLI only, never to a shell job.
 * OPENCLAW_BIN names bin/openclaw (the core's bundled CLI, also what the scripts run) when
 * it is there; the image has it on PATH as `openclaw`.
 */
export function wireCron(
  ctx: CoreContext,
  gatewayEnv: () => Record<string, string>,
): { start: () => Promise<void>; stop: () => Promise<void> } {
  const wrapper = join(ctx.root, "bin", "openclaw");
  const bin = existsSync(wrapper) ? { OPENCLAW_BIN: wrapper } : {};
  setGatewayEnv(() => ({ ...gatewayEnv(), ...bin }));
  const timeout = cronShellTimeoutSecs(ctx.env, ctx.root);
  if (timeout !== DEFAULT_SHELL_TIMEOUT_SECS) {
    ctx.log("warn", "[cron] shell_timeout_secs differs from the cron module's own limit and is not applied", { configured: timeout, used: DEFAULT_SHELL_TIMEOUT_SECS });
  }
  return { start: () => startCron(ctx), stop: () => stopCron() };
}

export async function startCore(env: Env, opts: StartOptions = {}): Promise<RunningCore> {
  const paths = resolvePaths(env, opts.cwd);
  const { host, port } = serveSettings(env);
  const token = resolveToken(env, paths.secretsDir);
  const log: CoreContext["log"] =
    opts.log ??
    ((level, msg, fields) => {
      if (level === "debug") return;
      process.stderr.write(`[teeny-core] ${level} ${msg}${fields ? ` ${JSON.stringify(fields)}` : ""}\n`);
    });

  mkdirSync(paths.openclawDir, { recursive: true, mode: 0o700 });
  let sealer: Sealer | undefined;
  try {
    sealer = Sealer.fromFile(masterKeyFile(env, paths.secretsDir));
  } catch (e) {
    log("warn", "no master key; secrets cannot be stored", { error: e instanceof Error ? e.message : String(e) });
  }
  const store = new SettingsStore(paths.stateDir, sealer);
  const catalog = new AgentCatalog(paths.brainDir, paths.openclawDir);
  const secretFiles = { gatewayToken: gatewayTokenFile(paths.openclawDir), modelKey: join(paths.openclawDir, "model.key") };
  ensureGatewayToken(paths.openclawDir);
  const gatewayPort = Number(env.OPENCLAW_GATEWAY_PORT || DEFAULT_GATEWAY_PORT);
  const configPath = join(paths.openclawDir, "openclaw.json");
  const guardrails = guardrailsIfPresent(join(paths.root, "core", "plugins", "teeny-guardrails"));

  /** The model key, as a private file the Gateway reads through a SecretRef: never in its environment. */
  const writeModelKey = (): void => {
    writeFileSync(secretFiles.modelKey, `${store.localAiApiKey() || "unset"}\n`, { mode: 0o600 });
    chmodSync(secretFiles.modelKey, 0o600);
  };
  /** What the running Gateway was started on: the config text and the model key it reads. */
  let applied = "";
  const writeConfig = (): void => {
    writeModelKey();
    const s = store.snapshot;
    const defaultModel = s.models.default_model ? bareModel(s.models.default_model) : env.SPINE_MODEL;
    const registry = s.modelRegistry.filter((m) => m.provider === "local-openai");
    const ids = new Map<string, number | undefined>();
    if (defaultModel) ids.set(defaultModel, undefined);
    for (const m of registry) ids.set(m.id, m.context_window);
    const config = buildGatewayConfig({
      port: gatewayPort,
      modelUrl: env.LOCAL_OPENAI_URL,
      model: defaultModel,
      models: [...ids].map(([id, contextWindow]) => ({ id, ...(contextWindow ? { contextWindow } : {}) })),
      compactionTriggerTokens: compactionTrigger(env),
      keepRecentTokens: KEEP_RECENT_TOKENS,
      agentTimeoutSecs: store.agentTimeoutSecs,
      defaultWorkspace: join(paths.openclawDir, "workspaces"),
      agents: catalog.entries(),
      ...(catalog.has("simple") ? { systemAgentId: "simple" } : {}),
      secretFiles,
      ...(guardrails
        ? {
            guardrailsPath: guardrails,
            guardrailsPolicy: buildGuardrailsPolicy({ defs: catalog.definitions(), paths, env, workspaceOf: (id) => catalog.workspace(id) }),
          }
        : {}),
      ...(findChrome(env) ? { chromePath: findChrome(env) as string } : {}),
    });
    const text = JSON.stringify(config, null, 2);
    writeFileSync(configPath, text, { mode: 0o600 });
    chmodSync(configPath, 0o600);
    applied = `${text}\n${store.localAiApiKey() ?? ""}`;
  };
  /** The environment of the Gateway and of its CLI: no core token, no master key file path, nothing else of ours. */
  const gatewayEnv = (): Record<string, string> => {
    const out: Record<string, string> = {
      PATH: env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
      HOME: env.HOME ?? paths.openclawDir,
      LANG: env.LANG ?? "C.UTF-8",
      OPENCLAW_STATE_DIR: paths.openclawDir,
      OPENCLAW_CONFIG_PATH: configPath,
    };
    for (const k of ["TMPDIR", "CHROME_BIN", "NODE_OPTIONS"]) if (env[k]) out[k] = env[k] as string;
    return out;
  };

  const runner = new DrainingRunner(opts.runner ?? new CliAgentRunner({ env: gatewayEnv }));
  const supervisor = opts.noGateway
    ? undefined
    : new GatewaySupervisor({
        port: gatewayPort,
        log: (l, m, f) => log(l, m, f),
        prepare: () => {
          writeConfig();
          return gatewayEnv();
        },
        ...(opts.spawn ? { spawn: opts.spawn } : {}),
      });

  /**
   * Settings changed. Rewrite the Gateway's config; restart it only when what it reads
   * actually changed, and then only after the turns in flight have finished (new ones
   * wait at the door), so a restart never ends an evolve generation or a cron turn.
   */
  const applySettings = async (): Promise<void> => {
    try {
      if (!supervisor) {
        writeConfig();
        return;
      }
      const before = applied;
      writeConfig();
      if (applied === before) return;
      const drained = await runner.quiesce(opts.drainMs ?? DRAIN_MS);
      if (!drained) log("warn", "restarting the gateway with turns still running", { running: runner.running });
      try {
        await supervisor.restartNow();
      } finally {
        runner.release();
      }
    } catch (e) {
      runner.release();
      log("error", "could not apply settings to the gateway", { error: e instanceof Error ? e.message : String(e) });
    }
  };

  let debounce: NodeJS.Timeout | undefined;
  const ctx: CoreContext = {
    env,
    root: paths.root,
    stateDir: paths.stateDir,
    log,
    services: {
      paths,
      store,
      sealer,
      runner,
      costLog: new CostLog(join(paths.openclawDir, "cost.jsonl")),
      gatewayRunning: () => supervisor?.running ?? false,
      configChanged: () => {
        // bin/spine sends several settings in a row at boot; one restart serves them all.
        clearTimeout(debounce);
        debounce = setTimeout(() => void applySettings(), 500);
      },
      hasAgent: (id) => catalog.has(id),
      prepareAgent: (id) => void catalog.prepare(id),
      findChrome: () => findChrome(env),
    },
  };

  const cron = wireCron(ctx, gatewayEnv);
  const server = createHttpServer({ registry: buildRegistry(), token, ctx });
  server.listen(port, host);
  await once(server, "listening");
  const bound = (server.address() as AddressInfo).port;
  if (supervisor) await supervisor.start();
  await cron.start().catch((e: unknown) => log("error", "cron could not start", { error: e instanceof Error ? e.message : String(e) }));
  log("info", "serving", { host, port: bound });

  return {
    host,
    port: bound,
    stop: async () => {
      clearTimeout(debounce);
      await cron.stop();
      await supervisor?.stop();
      server.closeAllConnections();
      await new Promise<void>((r) => server.close(() => r()));
    },
  };
}

export async function run(argv: readonly string[], env: Env = process.env): Promise<number> {
  const [command] = argv;
  if (command === "giveaway-tx") return giveawayTxCli(argv.slice(1));
  if (command === "openclaw") return openclawPassthrough(argv.slice(1), env);
  if (command !== "serve") {
    process.stderr.write(`${USAGE}\n`);
    return 2;
  }
  let core: RunningCore;
  try {
    core = await startCore(env);
  } catch (e) {
    process.stderr.write(`teeny-core: ${e instanceof Error ? e.message : String(e)}\n`);
    return 1;
  }
  return new Promise<number>((resolve) => {
    const down = (): void => void core.stop().then(() => resolve(0));
    process.once("SIGTERM", down);
    process.once("SIGINT", down);
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  void run(process.argv.slice(2)).then((code) => process.exit(code));
}
