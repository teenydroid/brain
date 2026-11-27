#!/usr/bin/env node
// teeny-core: the OpenClaw-backed core. `teeny-core serve` answers the
// openhuman.* JSON-RPC on $OPENHUMAN_CORE_HOST:$OPENHUMAN_CORE_PORT and
// supervises the OpenClaw Gateway its agent turns run on.
import { randomBytes } from "node:crypto";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { AgentCatalog } from "./agents/provision.js";
import { CostLog } from "./agents/cost.js";
import { bareModel } from "./agents/model.js";
import { findChrome } from "./config/chrome.js";
import { resolvePaths, serveSettings } from "./config/paths.js";
import { Sealer, masterKeyFile } from "./config/secrets.js";
import { SettingsStore } from "./config/store.js";
import { resolveToken } from "./config/token.js";
import { CliAgentRunner, type AgentRunner } from "./gateway/client.js";
import { GATEWAY_TOKEN_ENV, MODEL_KEY_ENV, buildGatewayConfig, guardrailsIfPresent } from "./gateway/config.js";
import { GatewaySupervisor, type SpawnFn } from "./gateway/supervisor.js";
import { createHttpServer } from "./server/http.js";
import { buildRegistry } from "./server/registry.js";
import type { CoreContext } from "./server/types.js";

const USAGE = "usage: teeny-core serve";
/** bin/lib/core_sections/context.py: COMPACTION_TRIGGER_TOKENS and KEEP_RECENT_TOKENS. */
const COMPACTION_TRIGGER_TOKENS = 100_000;
const KEEP_RECENT_TOKENS = 40_000;
const DEFAULT_GATEWAY_PORT = 18789;

type Env = Readonly<Record<string, string | undefined>>;

export interface StartOptions {
  cwd?: string;
  /** Replace the agent runner (tests). */
  runner?: AgentRunner;
  /** Do not start the Gateway child (tests). */
  noGateway?: boolean;
  spawn?: SpawnFn;
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
  const gatewayToken = randomBytes(32).toString("hex");
  const gatewayPort = Number(env.OPENCLAW_GATEWAY_PORT || DEFAULT_GATEWAY_PORT);
  const configPath = join(paths.openclawDir, "openclaw.json");
  const guardrails = guardrailsIfPresent(join(paths.root, "core", "plugins", "teeny-guardrails"));

  const writeConfig = (): void => {
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
      ...(guardrails ? { guardrailsPath: guardrails } : {}),
    });
    writeFileSync(configPath, JSON.stringify(config, null, 2), { mode: 0o600 });
    chmodSync(configPath, 0o600);
  };

  /** The environment of the Gateway and of its CLI: no core token, no master key file path, nothing else of ours. */
  const gatewayEnv = (): Record<string, string> => {
    const out: Record<string, string> = {
      PATH: env.PATH ?? "/usr/local/bin:/usr/bin:/bin",
      HOME: env.HOME ?? paths.openclawDir,
      LANG: env.LANG ?? "C.UTF-8",
      OPENCLAW_STATE_DIR: paths.openclawDir,
      OPENCLAW_CONFIG_PATH: configPath,
      [GATEWAY_TOKEN_ENV]: gatewayToken,
    };
    for (const k of ["TMPDIR", "CHROME_BIN", "NODE_OPTIONS"]) if (env[k]) out[k] = env[k] as string;
    const key = store.localAiApiKey();
    if (key) out[MODEL_KEY_ENV] = key;
    return out;
  };

  const runner = opts.runner ?? new CliAgentRunner({ env: gatewayEnv });
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
        debounce = setTimeout(() => {
          try {
            if (supervisor) void supervisor.restartNow();
            else writeConfig();
          } catch (e) {
            log("error", "could not apply settings to the gateway", { error: e instanceof Error ? e.message : String(e) });
          }
        }, 500);
      },
      hasAgent: (id) => catalog.has(id),
      prepareAgent: (id) => void catalog.prepare(id),
      findChrome: () => findChrome(env),
    },
  };

  const server = createHttpServer({ registry: buildRegistry(), token, ctx });
  server.listen(port, host);
  await once(server, "listening");
  const bound = (server.address() as AddressInfo).port;
  if (supervisor) await supervisor.start();
  log("info", "serving", { host, port: bound });

  return {
    host,
    port: bound,
    stop: async () => {
      clearTimeout(debounce);
      await supervisor?.stop();
      server.closeAllConnections();
      await new Promise<void>((r) => server.close(() => r()));
    },
  };
}

export async function run(argv: readonly string[], env: Env = process.env): Promise<number> {
  const [command] = argv;
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
