// The browser fences. OpenHuman: the browser opened any public https page but
// never a host on deploy/browser-deny.txt (OPENHUMAN_BROWSER_DENY_FILE; an
// unreadable list refused every page); the mind's and its worker's browser
// was view-only (`browser_view_only`); the operator's took only
// `unattended_actions`; passwords came from bin/keychain-fill for the
// {{keychain:<site>}} placeholder, and only on that site's own pages; the
// pictures of pages were kept, newest 24, in $TEENY_BROWSER_PICTURES.
import { execFile } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync } from "node:fs";
import { basename, extname, join } from "node:path";
import { isIP } from "node:net";
import type { BrowserPolicy } from "./policy.js";

/** hosts of a list file: one per line, `#` comments, lowercased; throws when unreadable or empty of hosts. */
export function readHostList(file: string | undefined, what: string, allowEmpty = false): string[] {
  if (!file) throw new Error(`no ${what} is configured`);
  const text = readFileSync(file, "utf8");
  const hosts: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.split("#", 1)[0]?.trim().toLowerCase().replace(/^\.+|\.+$/g, "") ?? "";
    if (line && !hosts.includes(line)) hosts.push(line);
  }
  if (hosts.length === 0 && !allowEmpty) throw new Error(`${what} lists no host`);
  return hosts;
}

/** A host matches itself and every subdomain of an entry. */
export function hostOnList(host: string, list: readonly string[]): boolean {
  const h = host.toLowerCase().replace(/\.$/, "");
  return list.some((entry) => h === entry || h.endsWith(`.${entry}`));
}

function privateAddress(host: string): boolean {
  const bare = host.replace(/^\[|\]$/g, "");
  const kind = isIP(bare);
  if (kind === 4) {
    const [a, b] = bare.split(".").map(Number) as [number, number];
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127);
  }
  if (kind === 6) return /^(::1?|fe[89ab]|f[cd])/i.test(bare) || bare.toLowerCase().startsWith("::ffff:");
  return host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal");
}

export interface UrlVerdict {
  ok: boolean;
  reason?: string;
  host?: string;
}

/** May the browser open `raw`: public https, no credentials, not on the deny list. */
export function judgeUrl(raw: unknown, denyFile: string | undefined): UrlVerdict {
  if (typeof raw !== "string" || raw.trim() === "") return { ok: false, reason: "the browser needs a url" };
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, reason: `${raw} is not a URL` };
  }
  if (url.protocol !== "https:") return { ok: false, reason: `the browser opens https pages only (${url.protocol.replace(":", "")} refused)` };
  if (url.username || url.password) return { ok: false, reason: "a URL with credentials is refused" };
  if (privateAddress(url.hostname)) return { ok: false, reason: `${url.hostname} is not a public address` };
  let deny: string[];
  try {
    deny = readHostList(denyFile, "browser deny list", true);
  } catch (e) {
    // OpenHuman: "an unreadable list refuses every page".
    return { ok: false, reason: `the browser deny list cannot be read, so no page opens (${(e as Error).message})` };
  }
  if (hostOnList(url.hostname, deny)) return { ok: false, reason: `${url.hostname} is on the operator's browser deny list` };
  return { ok: true, host: url.hostname.toLowerCase() };
}

/** Raw http tools (web_fetch, http_request) may reach only deploy/browser-allow.txt. */
export function judgeAllowedFetch(raw: unknown, allowFile: string | undefined): UrlVerdict {
  if (typeof raw !== "string") return { ok: false, reason: "the call needs a url" };
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, reason: `${raw} is not a URL` };
  }
  if (url.protocol !== "https:" || url.username || url.password) return { ok: false, reason: "only plain https URLs are allowed" };
  let allow: string[];
  try {
    allow = readHostList(allowFile, "browser allow list");
  } catch (e) {
    return { ok: false, reason: `the allow list cannot be read (${(e as Error).message})` };
  }
  if (!hostOnList(url.hostname, allow)) return { ok: false, reason: `${url.hostname} is not on deploy/browser-allow.txt` };
  return { ok: true, host: url.hostname.toLowerCase() };
}

/** The unattended `act` kinds that read a page without changing it, always allowed with `act`. */
const QUIET_KINDS = ["wait", "scroll", "scrollIntoView", "hover"];

function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) for (const v of value) strings(v, out);
  else if (value && typeof value === "object") for (const v of Object.values(value)) strings(v, out);
  return out;
}

function mapStrings<T>(value: T, fn: (s: string) => string): T {
  if (typeof value === "string") return fn(value) as unknown as T;
  if (Array.isArray(value)) return value.map((v) => mapStrings(v, fn)) as unknown as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, mapStrings(v, fn)])) as unknown as T;
  }
  return value;
}

const PLACEHOLDER = /\{\{keychain:([A-Za-z0-9.-]+)\}\}/g;

export interface BrowserState {
  /** host last opened or navigated to, per session+tab */
  hosts: Map<string, string>;
  /** passwords typed in this process, to mask in results */
  secrets: Set<string>;
}

export function newBrowserState(): BrowserState {
  return { hosts: new Map(), secrets: new Set() };
}

export interface BrowserDeps {
  denyFile: string | undefined;
  keychainFill: string | undefined;
  /** runs bin/keychain-fill <host>; resolves with its stdout. Injectable for tests. */
  fill: (program: string, host: string) => Promise<string>;
}

export function runKeychainFill(program: string, host: string): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(program, [host], { timeout: 10_000, maxBuffer: 64 * 1024, env: { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "" } }, (err, stdout) => {
      if (err) reject(new Error("the keychain did not answer"));
      else resolve(stdout.replace(/\r?\n$/, ""));
    });
  });
}

export type BrowserVerdict = { block: string } | { params?: Record<string, unknown> } | undefined;

function tabKey(sessionKey: string | undefined, params: Record<string, unknown>): string {
  return `${sessionKey ?? ""}|${typeof params.targetId === "string" ? params.targetId : ""}`;
}

/** Judges one browser tool call for an agent. */
export async function judgeBrowser(
  policy: BrowserPolicy,
  deps: BrowserDeps,
  state: BrowserState,
  sessionKey: string | undefined,
  params: Record<string, unknown>,
): Promise<BrowserVerdict> {
  const action = params.action;
  if (typeof action !== "string") return { block: "the browser call has no action" };
  if (!policy.actions.includes(action)) return { block: `browser action ${action} is not allowed for this agent` };
  // Routing: only the host's own managed browser, on a profile this agent was given.
  if (params.node !== undefined && params.node !== "") return { block: "browser node routing is not allowed" };
  if (params.target !== undefined && params.target !== "host") return { block: "the browser runs on the gateway host only" };
  if (params.dashboard !== undefined) return { block: "browser dashboards are not allowed" };
  if (params.profile !== undefined && params.profile !== policy.profile) {
    return { block: `browser profile ${String(params.profile)} is not this agent's` };
  }

  const key = tabKey(sessionKey, params);
  if (action === "open" || action === "navigate") {
    const v = judgeUrl(params.url, deps.denyFile);
    if (!v.ok) return { block: v.reason ?? "refused" };
    state.hosts.set(key, v.host as string);
    state.hosts.set(`${sessionKey ?? ""}|`, v.host as string);
    return undefined;
  }
  if (action !== "act") {
    // Any other action that carries a URL is judged like open.
    for (const k of ["url", "targetUrl"]) {
      if (params[k] !== undefined) {
        const v = judgeUrl(params[k], deps.denyFile);
        if (!v.ok) return { block: v.reason ?? "refused" };
      }
    }
    return undefined;
  }

  // act: params.request is the action body (or its fields sit beside `action`).
  const body = (params.request && typeof params.request === "object" ? params.request : params) as Record<string, unknown>;
  const kinds = collectKinds(body);
  if (kinds.length === 0) return { block: "browser act without a kind" };
  const allowedKinds = [...policy.actKinds, ...(policy.actKinds.length ? QUIET_KINDS : [])];
  for (const k of kinds) {
    if (!allowedKinds.includes(k)) return { block: `browser act ${k} is not allowed for this agent` };
  }
  // The keychain placeholder: swapped for the real password only on that site's own page.
  const all = strings(body);
  const placeholders = all.flatMap((s) => [...s.matchAll(PLACEHOLDER)].map((m) => (m[1] as string).toLowerCase()));
  if (placeholders.length === 0) return undefined;
  if (!policy.keychain || !deps.keychainFill) return { block: "this agent may not use the keychain placeholder" };
  if (!kinds.every((k) => k === "type" || k === "fill")) return { block: "the keychain placeholder can only be typed or filled" };
  const host = state.hosts.get(key) ?? state.hosts.get(`${sessionKey ?? ""}|`);
  if (!host) return { block: "the browser has not opened a page this guard saw, so it cannot tell which site is typed into" };
  const sites = new Set(placeholders);
  if (sites.size !== 1) return { block: "one keychain site per call" };
  const site = placeholders[0] as string;
  if (host !== site && !host.endsWith(`.${site}`)) {
    return { block: `the keychain placeholder for ${site} is typed only on ${site}'s own pages (this tab is on ${host})` };
  }
  let secret: string;
  try {
    secret = await deps.fill(deps.keychainFill, host);
  } catch {
    return { block: "the keychain gave no password for this site" };
  }
  if (!secret) return { block: "the keychain gave no password for this site" };
  state.secrets.add(secret);
  const filled = mapStrings(params, (s) => s.replace(PLACEHOLDER, () => secret));
  return { params: filled };
}

function collectKinds(body: Record<string, unknown>): string[] {
  const kinds: string[] = [];
  if (typeof body.kind === "string") kinds.push(body.kind);
  if (Array.isArray(body.actions)) {
    for (const a of body.actions) {
      if (a && typeof a === "object") kinds.push(...collectKinds(a as Record<string, unknown>));
      else kinds.push("?");
    }
  }
  // A batch is only its steps; a batch without readable steps is refused.
  if (body.kind === "batch" && !Array.isArray(body.actions)) kinds.push("?");
  return Array.isArray(body.actions) ? kinds.filter((k) => k !== "batch") : kinds;
}

/** Replaces every typed password in a tool result with a mask. */
export function maskSecrets<T>(value: T, secrets: ReadonlySet<string>): T {
  if (secrets.size === 0) return value;
  return mapStrings(value, (s) => {
    let out = s;
    for (const secret of secrets) if (secret && out.includes(secret)) out = out.split(secret).join("[keychain]");
    return out;
  });
}

// --- the screenshot sink: replaces spine/src/browser_pictures.rs -------------

const PICTURE = /^[0-9]{13}-[0-9]{6}\.(png|jpeg)$/;
let counter = 0;

/** `<unix ms, 13 digits>-<n, 6 digits>.png|jpeg`, so names sort in the order the pictures were taken. */
export function pictureName(takenAtMs: number, n: number, extension: string): string {
  const ext = extension === "jpg" || extension === "jpeg" ? "jpeg" : "png";
  return `${String(takenAtMs).padStart(13, "0")}-${String(n % 1_000_000).padStart(6, "0")}.${ext}`;
}

/** Keeps the newest `keep` pictures this sink wrote; touches nothing else in the folder. */
export function prunePictures(dir: string, keep: number): void {
  let names: string[];
  try {
    names = readdirSync(dir).filter((n) => PICTURE.test(n) && statSync(join(dir, n)).isFile());
  } catch {
    return;
  }
  names.sort();
  for (const old of names.slice(0, Math.max(0, names.length - keep))) rmSync(join(dir, old), { force: true });
}

/** Copies one picture into the sink whole or not at all (a dot-name first, then a rename), then prunes. */
export function savePicture(dir: string, source: string, keep: number, now: number = Date.now()): string | undefined {
  const ext = extname(source).slice(1).toLowerCase();
  if (!["png", "jpg", "jpeg"].includes(ext) || !existsSync(source)) return undefined;
  mkdirSync(dir, { recursive: true });
  const name = pictureName(now, counter++, ext);
  const partial = join(dir, `.${name}.part`);
  copyFileSync(source, partial);
  renameSync(partial, join(dir, name));
  prunePictures(dir, keep);
  return join(dir, name);
}

/** Image file paths a tool result names (best effort: the result shape is the browser plugin's). */
export function picturePaths(result: unknown): string[] {
  const out = new Set<string>();
  for (const s of strings(result)) {
    for (const m of s.matchAll(/(\/[^\s"'<>]+\.(?:png|jpe?g))/gi)) out.add(m[1] as string);
  }
  return [...out].filter((p) => basename(p).length > 0);
}
