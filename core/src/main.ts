#!/usr/bin/env node
// teeny-core: the OpenClaw-backed core. `teeny-core serve` answers the
// openhuman.* JSON-RPC on $OPENHUMAN_CORE_HOST:$OPENHUMAN_CORE_PORT and
// supervises the OpenClaw Gateway its agent turns run on.
import "./quiet.js";
import { chmodSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { once } from "node:events";
import { connect, type AddressInfo } from "node:net";
import { dirname, join } from "node:path";
import { AgentCatalog } from "./agents/provision.js";
import { CostLog } from "./agents/cost.js";
import { bareModel } from "./agents/model.js";
import { resolvePaths, serveSettings } from "./config/paths.js";
import { ensurePrivateDir, tightenTree, unsafeAncestors } from "./config/private.js";
import { Sealer, masterKeyFile } from "./config/secrets.js";
import { SettingsStore } from "./config/store.js";
import { resolveToken } from "./config/token.js";
import { CliAgentRunner, type AgentRunner } from "./gateway/client.js";
import { buildGuardrailsPolicy, guardrailsReadyFile } from "./agents/guardrails.js";
import { findChrome } from "./config/chrome.js";
import { buildGatewayConfig, guardrailsIfPresent } from "./gateway/config.js";
import { ensureGatewayToken, gatewayTokenFile, openclawPassthrough } from "./gateway/passthrough.js";
import { GatewaySupervisor, type SpawnFn } from "./gateway/supervisor.js";
import { parse as parseToml } from "smol-toml";
import { setGatewayEnv, setShellTimeoutSecs, startCron, stopCron } from "./cron/index.js";
import { GuardedRunner } from "./gateway/guarded.js";
import { removeStaleApprovals } from "./gateway/approvals.js";
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
const SETTLE_MS = 500;
const BOOT_SETTLE_MS = 15_000;
const BOOT_WINDOW_MS = 120_000;
/** A burst of settings calls is applied at the latest this long after its first. */
const MAX_SETTLE_MS = 60_000;

type Env = Readonly<Record<string, string | undefined>>;

/** Whether something accepts connections on 127.0.0.1:port: the Gateway's listener, probed by the supervisor's gate. */
export function portOpen(port: number, timeoutMs = 500): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = connect({ host: "127.0.0.1", port });
    const done = (ok: boolean): void => {
      sock.destroy();
      resolve(ok);
    };
    sock.setTimeout(timeoutMs, () => done(false));
    sock.once("connect", () => done(true));
    sock.once("error", () => done(false));
  });
}

export interface StartOptions {
  cwd?: string;
  /** Replace the agent runner (tests). */
  runner?: AgentRunner;
  /** Do not start the Gateway child (tests). */
  noGateway?: boolean;
  spawn?: SpawnFn;
  /** How long a settings-driven Gateway restart waits for running turns (tests). */
  drainMs?: number;
  /** Quiet time after a settings call before the Gateway's config is compared and, if changed, applied. Default 500 ms. */
  settleMs?: number;
  /** The same, while the core is young (bin/spine sends its settings in a burst, seconds apart). Default 15 s. */
  bootSettleMs?: number;
  /** How long after serving counts as boot. Default 2 minutes. */
  bootWindowMs?: number;
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
    const n = cron?.shell_timeout_secs;
    // Only a TOML number counts: Number(true) is 1, and a one-second timeout is not what `= true` meant.
    if (typeof n === "number" && Number.isInteger(n) && n > 0) return n;
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
  setShellTimeoutSecs(cronShellTimeoutSecs(ctx.env, ctx.root));
  return { start: () => startCron(ctx), stop: () => stopCron() };
}

/**
 * umask 077 for the core and everything it spawns (the Gateway, its CLI calls): a directory
 * made under the box's umask 002 is group-writable, and OpenClaw refuses to start under one.
 * Put back when the core stops.
 */
export async function startCore(env: Env, opts: StartOptions = {}): Promise<RunningCore> {
  const previous = process.umask(0o077);
  try {
    const core = await startCoreInner(env, opts);
    return {
      ...core,
      stop: async () => {
        await core.stop();
        process.umask(previous);
      },
    };
  } catch (e) {
    process.umask(previous);
    throw e;
  }
}

async function startCoreInner(env: Env, opts: StartOptions): Promise<RunningCore> {
  const paths = resolvePaths(env, opts.cwd);
  const { host, port } = serveSettings(env);
  const token = resolveToken(env, paths.secretsDir);
  const log: CoreContext["log"] =
    opts.log ??
    ((level, msg, fields) => {
      if (level === "debug") return;
      process.stderr.write(`[teeny-core] ${level} ${msg}${fields ? ` ${JSON.stringify(fields)}` : ""}\n`);
    });

  // OpenClaw refuses to start ("No safe OpenClaw temp root") under a directory others can write,
  // and a state/ made under umask 002 is one. The core makes state/openclaw and its log directory
  // itself, private, and takes group/other write off what is already there.
  ensurePrivateDir(paths.openclawDir);
  const logDir = join(paths.openclawDir, "logs");
  ensurePrivateDir(logDir);
  tightenTree(paths.openclawDir);
  for (const file of removeStaleApprovals(paths.openclawDir)) log("warn", "removed a stale exec-approvals file: it would make a shell agent's exec stricter than the config says", { file });
  for (const dir of unsafeAncestors(paths.openclawDir)) {
    log("warn", "a directory above state/openclaw is writable by group or others; OpenClaw will not keep temp files under it (chmod go-w it)", { dir });
  }
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
  /** What the Gateway was last started on (set by prepare, never by a mere rewrite): the config text and the model key it reads. */
  let startedWith = "";
  /** Writes the Gateway's config (exec denied for shell agents unless `guardrailsProven`) and the model key file; returns the text a Gateway started now would read. */
  const writeConfig = (guardrailsProven = false): string => {
    writeModelKey();
    const s = store.snapshot;
    const defaultModel = s.models.default_model ? bareModel(s.models.default_model) : env.SPINE_MODEL;
    const registry = s.modelRegistry.filter((m) => m.provider === "local-openai");
    const ids = new Map<string, number | undefined>();
    if (defaultModel) ids.set(defaultModel, undefined);
    for (const m of registry) ids.set(m.id, m.context_window);
    for (const pinned of catalog.pinnedModels()) if (!ids.has(bareModel(pinned))) ids.set(bareModel(pinned), undefined);
    // Shell agents get exec only once the plugin is proven loaded in this Gateway (supervisor gate); `applied`
    // is the proven shape, so that opening exec later is never mistaken for a change that needs a restart.
    const build = (proven: boolean): Record<string, unknown> => buildGatewayConfig({
      port: gatewayPort,
      guardrailsProven: proven,
      modelUrl: env.LOCAL_OPENAI_URL,
      model: defaultModel,
      models: [...ids].map(([id, contextWindow]) => ({ id, ...(contextWindow ? { contextWindow } : {}) })),
      compactionTriggerTokens: compactionTrigger(env),
      keepRecentTokens: KEEP_RECENT_TOKENS,
      agentTimeoutSecs: store.agentTimeoutSecs,
      defaultWorkspace: join(paths.openclawDir, "workspaces"),
      agents: catalog.entries(),
      skillDirs: [join(paths.brainDir, "skills", "hands")],
      ...(catalog.has("simple") ? { systemAgentId: "simple" } : {}),
      secretFiles,
      ...(guardrails
        ? {
            guardrailsPath: guardrails,
            guardrailsPolicy: buildGuardrailsPolicy({ defs: catalog.definitions(), paths, env, workspaceOf: (id) => catalog.workspace(id) }),
          }
        : {}),
      ...(findChrome(env) ? { chromePath: findChrome(env) as string } : {}),
      logFile: join(logDir, "openclaw-YYYY-MM-DD.log"),
    });
    const settled = JSON.stringify(build(true), null, 2);
    // The proven shape is what is compared, so opening exec later is never mistaken for a change that needs a restart.
    const shape = `${settled}\n${store.localAiApiKey() ?? ""}`;
    // Exec is written open only when nothing else differs from what the running Gateway was started on. The
    // Gateway hot-applies that one change (reload mode "hybrid"); any other change may make it restart itself, and
    // that restart must come up on "deny" until the core restarts it and the plugin is proven again.
    const opens = guardrailsProven && shape === startedWith;
    const text = opens ? settled : JSON.stringify(build(false), null, 2);
    writeFileSync(configPath, text, { mode: 0o600 });
    chmodSync(configPath, 0o600);
    return shape;
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
    // hand.dry_run(): the environment can only force a rehearsal on, never off. So a truthy
    // DRY_RUN reaches the hands the agents' shells start; anything else is left out and the
    // hands read secrets/teeny.env as before.
    if (/^(1|true|yes|on)$/i.test((env.DRY_RUN ?? "").trim())) out.DRY_RUN = "1";
    return out;
  };

  const baseRunner = opts.runner ?? new CliAgentRunner({ env: gatewayEnv });
  const runner = new DrainingRunner(
    guardrails && !opts.noGateway
      ? new GuardedRunner(baseRunner, {
          proven: () => supervisor?.proven ?? false,
          holdsShell: (id) => catalog.definitions().some((d) => d.id === id && d.tools.includes("shell")),
        })
      : baseRunner,
  );
  const supervisor = opts.noGateway
    ? undefined
    : new GatewaySupervisor({
        port: gatewayPort,
        ...(guardrails
          ? {
              gate: {
                file: guardrailsReadyFile(paths.openclawDir),
                listening: () => portOpen(gatewayPort),
                // The Gateway started on a config that denies exec; the plugin is proven, so open it. The Gateway
                // hot-applies tools.exec.security (reload mode "hybrid"); if it does not, exec stays denied (fail closed).
                onProven: () => void writeConfig(true),
              },
            }
          : {}),
        log: (l, m, f) => log(l, m, f),
        prepare: () => {
          startedWith = writeConfig(false);
          return gatewayEnv();
        },
        ...(opts.spawn ? { spawn: opts.spawn } : {}),
      });

  /**
   * Settings changed. Rewrite the Gateway's config; restart it only when it differs from the
   * one the running Gateway was started on, and then only after the turns in flight have
   * finished (new ones wait at the door), so a restart never ends an evolve generation or a
   * cron turn. One apply runs at a time: a call that arrives during one is folded into a
   * single re-check afterwards, which finds nothing to do when the restart already carried it.
   */
  let applying: Promise<void> | undefined;
  let again = false;
  const applyOnce = async (): Promise<void> => {
    let owns = false; // true once this call closed the drain gate; only then may it open it
    try {
      const wanted = writeConfig(supervisor?.proven ?? false);
      if (!supervisor || wanted === startedWith) return;
      owns = true;
      const drained = await runner.quiesce(opts.drainMs ?? DRAIN_MS);
      if (!drained) log("warn", "restarting the gateway with turns still running", { running: runner.running });
      await supervisor.restartNow();
    } catch (e) {
      log("error", "could not apply settings to the gateway", { error: e instanceof Error ? e.message : String(e) });
    } finally {
      if (owns) runner.release();
    }
  };
  const applySettings = (): Promise<void> => {
    if (applying) {
      again = true;
      return applying;
    }
    applying = (async () => {
      do {
        again = false;
        await applyOnce();
      } while (again);
    })().finally(() => {
      applying = undefined;
    });
    return applying;
  };

  let debounce: NodeJS.Timeout | undefined;
  let burstStart = 0;
  let servingSince = Number.POSITIVE_INFINITY;
  /** bin/spine sends its settings one after another at boot, seconds apart (it asks the model server for windows between them): wait for a long quiet, once. */
  const settleMs = (): number =>
    Date.now() - servingSince < (opts.bootWindowMs ?? BOOT_WINDOW_MS) ? (opts.bootSettleMs ?? BOOT_SETTLE_MS) : (opts.settleMs ?? SETTLE_MS);
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
        // The file follows at once; the restart, if any, waits for the burst to end (and not past MAX_SETTLE_MS).
        try {
          writeConfig(supervisor?.proven ?? false);
        } catch (e) {
          log("error", "could not write the Gateway's config", { error: e instanceof Error ? e.message : String(e) });
        }
        const now = Date.now();
        if (!debounce) burstStart = now;
        clearTimeout(debounce);
        const wait = Math.max(0, Math.min(settleMs(), burstStart + MAX_SETTLE_MS - now));
        debounce = setTimeout(() => {
          debounce = undefined;
          void applySettings();
        }, wait);
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
  servingSince = Date.now();
  if (supervisor) await supervisor.start();
  await cron.start().catch((e: unknown) => log("error", "cron could not start", { error: e instanceof Error ? e.message : String(e) }));
  log("info", "serving", { host, port: bound });

  return {
    host,
    port: bound,
    stop: async () => {
      clearTimeout(debounce);
      debounce = undefined;
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
