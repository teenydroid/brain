// The policy the core generates for the plugin (plugins.entries.teeny-guardrails
// .config.policy). It is data only; the rules that read it are in the sibling
// modules. A policy that does not parse blocks every tool call.

/** What a playbook that runs natively may run, and how often it may act (core/src/playbooks/native.ts). */
export interface NativePolicy {
  playbook: string;
  maxPerDay: number;
  /** Calls to the acting hand one run may make. */
  maxPerRun: number;
  /** Each input's argv, `hands/<name>` first: the only input commands, run with DRY_RUN=1. */
  inputs: string[][];
  /** The one acting hand and its verbs; null for a playbook that only takes notes. */
  hand: { name: string; verbs: string[] } | null;
  /** Media helpers (hands/image generate, hands/x upload), allowed by prefix. */
  helpers: string[][];
  /** Names the playbook to the hand as TEENY_WRITER, as bin/act does for a `hand:` output. */
  writer?: string;
}

export interface ExecPolicy {
  /** Set for the agent of a native playbook: exec is held to exactly this. */
  native?: NativePolicy;
  /** May run hands/<name> (repo-relative or absolute), except `handsDeny`. */
  hands: boolean;
  /** Hands this agent never runs (hands that move money or hold keys). */
  handsDeny: string[];
  /** Absolute paths of the broker client this agent may run (state/home/bin/teeny). */
  teenyBins: string[];
  /** Plain program names this agent may run, read-only: ls, cat, grep ... */
  readOnly: string[];
  /** Programs that write files named on their command line (mkdir, cp ...); every such path is write-checked. */
  writers: string[];
  /** Programs that build or fetch (git, npm, make ...): allowed by name, their path arguments read-checked. */
  tools: string[];
}

export interface BrowserPolicy {
  /** Actions of the browser tool the agent may call. */
  actions: string[];
  /** Kinds of `act` it may take (empty: no act at all). */
  actKinds: string[];
  /** The browser profile every call is pinned to (the throwaway one for view-only agents). */
  profile?: string;
  /** May type the operator's {{keychain:<site>}} placeholder. */
  keychain: boolean;
}

export interface AgentPolicy {
  id: string;
  /** OpenClaw tool ids the agent may call at all (from brain/agents/<id>.toml). */
  tools: string[];
  /** Where relative paths start and where exec runs. */
  workspace: string;
  rw: string[];
  ro: string[];
  closed: string[];
  /** Folders the shell may write and read; the file tools' rw/ro when absent. */
  execRw?: string[];
  execRo?: string[];
  exec?: ExecPolicy;
  browser?: BrowserPolicy;
  /** Ids sessions_spawn may start. */
  subagents: string[];
  /** Tool calls one run may make (max_iterations in the agent's toml). */
  maxToolCalls: number;
}

export interface RecallPolicy {
  /** Agents whose turns get recalled memory. */
  agents: Record<string, { budgetTokens: number }>;
  /** state/memory-engine.json, written by memory_engine_set. */
  engineFile: string;
  /** The master key file the engine's API key is sealed under. */
  masterKeyFile: string;
  /** Wait for the pack at most this long. */
  timeoutMs: number;
}

export interface GuardrailsPolicy {
  root: string;
  stateDir: string;
  handsDir: string;
  home: string;
  systemForbidden: string[];
  browserDenyFile?: string;
  browserAllowFile?: string;
  keychainFill?: string;
  picturesDir?: string;
  picturesKeep: number;
  /** Written by the plugin once its hooks are registered: the core's proof that the gate is on. */
  readyFile?: string;
  agents: Record<string, AgentPolicy>;
  /** How to run `openclaw`, to read the browser's live tab URLs. */
  openclaw?: { cmd: string; args: string[] };
  recall?: RecallPolicy;
}

function isStringArray(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === "string");
}

function isArgvList(v: unknown): v is string[][] {
  return Array.isArray(v) && v.every((a) => isStringArray(a) && a.length > 0 && /^hands\/[a-z0-9][a-z0-9-]*$/.test(a[0] as string));
}

function parseNative(id: string, n: unknown): void {
  const e = n as Record<string, unknown> | null;
  const where = `teeny-guardrails: agents.${id}.exec.native`;
  if (typeof e !== "object" || e === null) throw new Error(`${where} must be an object`);
  if (typeof e.playbook !== "string" || !/^[a-z0-9][a-z0-9-]*$/.test(e.playbook)) throw new Error(`${where}.playbook must be a playbook name`);
  for (const k of ["maxPerDay", "maxPerRun"]) {
    if (!Number.isInteger(e[k]) || (e[k] as number) < 1) throw new Error(`${where}.${k} must be a positive integer`);
  }
  if (!isArgvList(e.inputs)) throw new Error(`${where}.inputs must be a list of hands/<name> argv lists`);
  if (!isArgvList(e.helpers)) throw new Error(`${where}.helpers must be a list of hands/<name> argv lists`);
  const hand = e.hand as { name?: unknown; verbs?: unknown } | null;
  if (hand !== null && (typeof hand !== "object" || typeof hand.name !== "string" || !isStringArray(hand.verbs) || hand.verbs.length === 0)) {
    throw new Error(`${where}.hand must be {name, verbs} or null`);
  }
  if (e.writer !== undefined && typeof e.writer !== "string") throw new Error(`${where}.writer must be a string`);
}

/** Reads the policy out of the plugin's config; throws on anything malformed. */
export function parsePolicy(config: unknown): GuardrailsPolicy {
  const p = (config as { policy?: unknown } | undefined)?.policy as Record<string, unknown> | undefined;
  if (!p || typeof p !== "object") throw new Error("teeny-guardrails: no policy in the plugin config");
  for (const k of ["root", "stateDir", "handsDir", "home"]) {
    if (typeof p[k] !== "string" || !(p[k] as string).startsWith("/")) throw new Error(`teeny-guardrails: policy.${k} must be an absolute path`);
  }
  if (!isStringArray(p.systemForbidden)) throw new Error("teeny-guardrails: policy.systemForbidden must be a list");
  if (p.readyFile !== undefined && (typeof p.readyFile !== "string" || !p.readyFile.startsWith("/"))) throw new Error("teeny-guardrails: policy.readyFile must be an absolute path");
  const agents = p.agents as Record<string, AgentPolicy> | undefined;
  if (!agents || typeof agents !== "object") throw new Error("teeny-guardrails: policy.agents is missing");
  for (const [id, a] of Object.entries(agents)) {
    if (a.id !== id) throw new Error(`teeny-guardrails: agent ${id} has a mismatched id`);
    for (const k of ["tools", "rw", "ro", "closed", "subagents"] as const) {
      if (!isStringArray(a[k])) throw new Error(`teeny-guardrails: agents.${id}.${k} must be a list`);
    }
    if (typeof a.workspace !== "string" || !a.workspace.startsWith("/")) throw new Error(`teeny-guardrails: agents.${id}.workspace must be absolute`);
    // Every nested policy that is present must be whole, so a malformed one fails here, at registration, not mid-turn.
    if (a.exec !== undefined) {
      const e = a.exec as unknown as Record<string, unknown>;
      if (typeof e !== "object" || e === null || typeof e.hands !== "boolean") throw new Error(`teeny-guardrails: agents.${id}.exec.hands must be a boolean`);
      for (const k of ["handsDeny", "teenyBins", "readOnly", "writers", "tools"]) {
        if (!isStringArray(e[k])) throw new Error(`teeny-guardrails: agents.${id}.exec.${k} must be a list`);
      }
      if (e.native !== undefined) parseNative(id, e.native);
    }
    if (a.browser !== undefined) {
      const b = a.browser as unknown as Record<string, unknown>;
      if (typeof b !== "object" || b === null) throw new Error(`teeny-guardrails: agents.${id}.browser must be an object`);
      for (const k of ["actions", "actKinds"]) {
        if (!isStringArray(b[k])) throw new Error(`teeny-guardrails: agents.${id}.browser.${k} must be a list`);
      }
      if (typeof b.keychain !== "boolean") throw new Error(`teeny-guardrails: agents.${id}.browser.keychain must be a boolean`);
      if (b.profile !== undefined && typeof b.profile !== "string") throw new Error(`teeny-guardrails: agents.${id}.browser.profile must be a string`);
    }
    for (const k of ["execRw", "execRo"] as const) {
      if (a[k] !== undefined && !isStringArray(a[k])) throw new Error(`teeny-guardrails: agents.${id}.${k} must be a list`);
    }
    if (!Number.isInteger(a.maxToolCalls) || a.maxToolCalls < 1) throw new Error(`teeny-guardrails: agents.${id}.maxToolCalls must be a positive integer`);
  }
  return {
    ...(p as unknown as GuardrailsPolicy),
    picturesKeep: typeof p.picturesKeep === "number" ? p.picturesKeep : 24,
  };
}
