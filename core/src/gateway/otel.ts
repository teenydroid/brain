// Tracing the Gateway's turns to Langfuse (deploy/langfuse/README.md). The Gateway exports OTLP/HTTP through
// OpenClaw's own `diagnostics-otel` plugin to the otel-collector on deploy/compose.yaml's private `langfuse-net`,
// and the collector forwards to the private Langfuse with Langfuse's key, which never reaches the spine.
//
// Switched on by TEENY_OTEL_URL in the core's environment (bin/spine exports it from TRACES_OTLP_URL in
// secrets/spine.env). TEENY_OTEL_CAPTURE=0 keeps prompts, replies and tool payloads off the spans.
//
// The plugin must be OpenClaw's official install, not a copy: the Gateway hands its diagnostic event stream only to
// a `diagnostics-otel` that is bundled or `trustedOfficialInstall` (an install record from the npm registry). A copy
// loaded through plugins.load.paths loads, logs "internal diagnostics capability unavailable" and exports nothing
// (live test, 2026-11-28). So the core installs `npm:@openclaw/diagnostics-otel@<the Gateway's version>` into the
// Gateway's own state with OpenClaw's installer, once, before the Gateway starts.
import { spawn } from "node:child_process";

type Env = Readonly<Record<string, string | undefined>>;

export const OTEL_PACKAGE = "@openclaw/diagnostics-otel";

/**
 * The compose services the Gateway may reach over plain http: deploy/compose.yaml's collector, on the private
 * langfuse-net. A named list, not any dotless host: Docker forwards a name it cannot resolve to the host's DNS, where
 * a search domain could turn `collector` into a machine off the box.
 */
export const OTEL_PLAIN_HOSTS: readonly string[] = ["otel-collector"];

/**
 * Whether the Gateway may send spans to `url`. Captured content can hold anything a turn saw, so the rule is the
 * memory plugin's (https, or plain http on this machine) widened by one case: plain http to a compose service in
 * OTEL_PLAIN_HOSTS. No credentials in the URL, no query: nothing secret goes in the Gateway's config.
 */
export function safeOtelEndpoint(url: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return false;
  const host = parsed.hostname.toLowerCase();
  if (!host) return false;
  if (parsed.protocol === "https:") return true;
  if (parsed.protocol !== "http:") return false;
  if (host === "localhost" || host === "[::1]" || /^127(\.\d{1,3}){3}$/.test(host)) return true;
  return OTEL_PLAIN_HOSTS.includes(host);
}

export interface OtelInput {
  endpoint: string;
  captureContent: boolean;
}

export type OtelSetting = { otel: OtelInput } | { otel: undefined; why?: string };

/** The `otel` input of buildGatewayConfig from the core's environment, or why tracing stays off. */
export function otelFromEnv(env: Env): OtelSetting {
  const endpoint = (env.TEENY_OTEL_URL ?? "").trim().replace(/\/+$/, "");
  if (!endpoint) return { otel: undefined };
  if (!safeOtelEndpoint(endpoint)) {
    return { otel: undefined, why: `TEENY_OTEL_URL is not https, loopback, or http to ${OTEL_PLAIN_HOSTS.join(", ")}; turns are not traced` };
  }
  const captureContent = !/^(0|false|no|off)$/i.test((env.TEENY_OTEL_CAPTURE ?? "").trim());
  return { otel: { endpoint, captureContent } };
}

/** Runs the OpenClaw CLI on the Gateway's state and config; resolves with its exit code and stdout. */
export type OpenclawRun = (args: string[], timeoutMs: number) => Promise<{ code: number; stdout: string }>;

export function spawnOpenclaw(command: { cmd: string; args: string[] }, env: Record<string, string>): OpenclawRun {
  return (args, timeoutMs) =>
    new Promise((resolve) => {
      const child = spawn(command.cmd, [...command.args, ...args], { env, stdio: ["ignore", "pipe", "pipe"] });
      let stdout = "";
      child.stdout.on("data", (d: Buffer) => (stdout += d.toString("utf8")));
      child.stderr.resume();
      const timer = setTimeout(() => child.kill("SIGTERM"), timeoutMs);
      child.on("error", () => {
        clearTimeout(timer);
        resolve({ code: 127, stdout });
      });
      // "close", not "exit": the child can exit with stdout still unread, and inspect's JSON would come up short.
      child.on("close", (code) => {
        clearTimeout(timer);
        resolve({ code: code ?? 1, stdout });
      });
    });
}

/** What `openclaw plugins inspect diagnostics-otel --json` says of the install, if it is the trusted one at `version`. */
export function trustedInstallAt(stdout: string, version: string): boolean {
  const start = stdout.indexOf("{");
  if (start < 0) return false;
  let report: Record<string, unknown>;
  try {
    report = JSON.parse(stdout.slice(start)) as Record<string, unknown>;
  } catch {
    return false;
  }
  const plugin = (report.plugin && typeof report.plugin === "object" ? report.plugin : report) as Record<string, unknown>;
  return plugin.id === "diagnostics-otel" && plugin.trustedOfficialInstall === true && plugin.version === version;
}

/**
 * Makes sure the Gateway's state holds the official diagnostics-otel at `version`: inspects it, and installs it from
 * the npm registry when it is missing or another version. `--no-enable`: the core's config owns activation, so the
 * installer touches no allowlist or entry. `--force`: over another version the installer refuses with "plugin already
 * exists" without it; with it the install is replaced and stays the trusted official one (checked 2026-11-28). Fails (and the caller leaves tracing off) when the registry cannot be
 * reached or the install is not trusted.
 *
 * The whole of it stays inside `budgetMs`: it runs before the core listens, and bin/spine fails the boot when the
 * core has not answered /health in SPINE_BOOT_SECS (120 s). Tracing is optional; a slow registry must cost the
 * boot a minute at most, then the core boots without it. (A healthy install takes about 15 s.)
 */
export const OTEL_ENSURE_BUDGET_MS = 60_000;

export async function ensureOtelPlugin(
  run: OpenclawRun,
  version: string,
  budgetMs: number = OTEL_ENSURE_BUDGET_MS,
  now: () => number = Date.now,
): Promise<{ ok: true; installed: boolean } | { ok: false; why: string }> {
  const deadline = now() + budgetMs;
  const timed = async (args: string[]): Promise<{ code: number; stdout: string } | undefined> => {
    const left = deadline - now();
    if (left <= 0) return undefined;
    const started = now();
    const result = await run(args, left);
    // The runner kills the CLI at `left`; a run that used all of it timed out, whatever its exit code.
    return now() - started >= left ? undefined : result;
  };
  const late = { ok: false as const, why: `installing ${OTEL_PACKAGE}@${version} took longer than ${Math.round(budgetMs / 1000)}s` };
  const inspect = (): Promise<{ code: number; stdout: string } | undefined> => timed(["plugins", "inspect", "diagnostics-otel", "--json"]);
  const first = await inspect();
  if (!first) return late;
  if (trustedInstallAt(first.stdout, version)) return { ok: true, installed: false };
  const install = await timed(["plugins", "install", `npm:${OTEL_PACKAGE}@${version}`, "--pin", "--no-enable", "--force"]);
  if (!install) return late;
  if (install.code !== 0) return { ok: false, why: `openclaw plugins install npm:${OTEL_PACKAGE}@${version} exited ${install.code}` };
  const second = await inspect();
  if (!second) return late;
  if (!trustedInstallAt(second.stdout, version)) {
    return { ok: false, why: `${OTEL_PACKAGE}@${version} installed but is not the trusted official install` };
  }
  return { ok: true, installed: true };
}
