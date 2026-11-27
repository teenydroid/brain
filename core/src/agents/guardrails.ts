// The policy the core hands the teeny-guardrails plugin: one entry per agent
// that holds a tool, built from brain/agents/*.toml and the posture
// bin/lib/core_sections/automation.py gave OpenHuman. Nothing here adds a
// tool: an agent's tool list is exactly its definition's, mapped by TOOL_MAP.
import { existsSync, readdirSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve, sep } from "node:path";
import { openclawCommand } from "../gateway/cli.js";
import type { CorePaths } from "../config/paths.js";
import { toolIds, type AgentDef } from "./definitions.js";

type Env = Readonly<Record<string, string | undefined>>;

/** automation.py DEFAULT_FORBIDDEN: OpenHuman's own default forbidden_paths. */
export const SYSTEM_FORBIDDEN = [
  "/etc", "/root", "/home", "/usr", "/bin", "/sbin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys",
  "/var", "/tmp", "~/.ssh", "~/.gnupg", "~/.aws", "~/.config",
];
/** automation.py BRAIN_FORBIDDEN: Teeny's own files in the brain, which no file tool reads or writes. */
export const BRAIN_FORBIDDEN = [
  "storage.db", "storage.db-wal", "storage.db-shm", "agents", "playbooks", "skills", "narrative", "assets",
  "cron.toml", "hooks.json", "calendar.json", "CONTEXT.md", "MEMORY.md", "MEMORY.seed.md", "agent",
];
/** automation.py CLOSED_TO_FILE_TOOLS: folders of the checkout no file tool may read. */
export const CLOSED_TO_FILE_TOOLS = ["secrets", ".git", "state", ".openhuman", "brain"];
/** automation.py LAB_EDIT_FORBIDDEN: what the evolver may not touch even in its own worktree. */
export const LAB_EDIT_FORBIDDEN = ["storage.db", "storage.db-wal", "storage.db-shm", "agent", "keyring", "dev-keychain.json", "state"];

/** Hands no agent runs through its shell: they move money or hold keys (agent_check.py NEVER, "no-model-pays"). */
export const HANDS_DENY = ["wallet", "x402", "giveaway", "bounty", "rent", "slopcash", "surplus", "keychain", "vps"];
const READ_ONLY = [
  "ls", "cat", "grep", "find", "echo", "pwd", "wc", "head", "tail", "date", "sort", "uniq", "diff", "which", "uname",
  "basename", "dirname", "tr", "cut", "realpath", "readlink", "stat", "file", "jq",
];
const WRITERS = ["mkdir", "touch", "cp", "mv", "ln"];
const TOOLS = ["git", "npm", "pnpm", "yarn", "cargo", "make", "cmake", "timeout"];

/** The browser actions that open and read a page (OpenHuman `browser_view_only`). */
export const VIEW_ACTIONS = ["status", "start", "stop", "tabs", "open", "focus", "close", "snapshot", "screenshot", "navigate", "text", "requests", "errors", "doctor"];
/** automation.py UNATTENDED_ACTIONS, minus task_step (OpenClaw has no browser task). */
export const UNATTENDED_KINDS = ["click", "fill", "type", "press", "select", "check"];

/** Browser profiles the generated config defines (gateway/config.ts). */
export const OPERATOR_PROFILE = "teeny";
export const VIEW_PROFILE = "view";

export interface PolicyInput {
  defs: AgentDef[];
  paths: CorePaths;
  env: Env;
  /** Where each agent's OpenClaw workspace (its AGENTS.md) is. */
  workspaceOf: (id: string) => string;
  home?: string;
}

/** The checkout's own top-level folders the file tools may read (automation.py code_roots). */
export function codeRoots(root: string, brain: string): string[] {
  const closed = new Set([...CLOSED_TO_FILE_TOOLS.map((n) => resolve(root, n)), resolve(brain)]);
  let names: string[];
  try {
    names = readdirSync(root).sort();
  } catch {
    return [];
  }
  const out: string[] = [];
  let realRoot: string;
  try {
    realRoot = realpathSync(root);
  } catch {
    return [];
  }
  for (const name of names) {
    const full = join(root, name);
    if (CLOSED_TO_FILE_TOOLS.includes(name)) continue;
    let st;
    try {
      st = statSync(full, { throwIfNoEntry: false });
    } catch {
      continue;
    }
    if (!st?.isDirectory()) continue;
    if (closed.has(full)) continue;
    // statSync follows links: a top-level link may grant only what stays inside the checkout.
    try {
      const real = realpathSync(full);
      if (real !== realRoot && !real.startsWith(realRoot + sep)) continue;
    } catch {
      continue;
    }
    out.push(full);
  }
  return out;
}

export function isLabEdit(env: Env): boolean {
  return env.SPINE_LAB === "1" && env.SPINE_LAB_EDIT === "1";
}

function perAgent(def: AgentDef, input: PolicyInput) {
  const { paths, env } = input;
  const root = paths.root;
  const brain = paths.brainDir;
  const workbench = join(paths.stateDir, "workbench");
  const ws = input.workspaceOf(def.id);
  const home = def.scheduledWorkspace ? resolve(root, def.scheduledWorkspace) : def.id === "worker" ? join(paths.stateDir, "home") : undefined;
  const hasShell = def.tools.includes("shell");
  const holdsBrowser = def.tools.includes("browser");
  const lab = isLabEdit(env) && def.id === "evolver";

  let rw: string[];
  let ro: string[];
  let closed: string[];
  let workspace: string;
  if (lab) {
    // The evolver on a lab spine: the candidate's worktree is the root, writable except its git link,
    // secrets, state and the core's own files.
    rw = [root];
    ro = [];
    workspace = root;
    closed = [
      join(root, ".git"), join(root, "secrets"), join(root, "state"), join(root, ".openhuman"),
      ...LAB_EDIT_FORBIDDEN.map((n) => join(brain, n)),
      join(root, "HYPOTHESIS.md"),
    ];
  } else {
    // SOUL.md and IDENTITY.md the core reserved for itself (automation.py: "the core reserves itself").
    closed = [join(root, "secrets"), join(root, ".openhuman"), join(root, ".git"), ...[...BRAIN_FORBIDDEN, "SOUL.md", "IDENTITY.md"].map((n) => join(brain, n))];
    ro = [...codeRoots(root, brain), ws];
    if (def.id === "operator") {
      // The desk: the task laid out for it and the report it leaves, nothing else.
      rw = [join(workbench, "desk")];
      ro = [];
      workspace = root;
    } else if (def.id === "evolver") {
      rw = [];
      workspace = root;
    } else {
      rw = [...(home ? [home] : []), ...(def.tools.includes("file_write") || def.tools.includes("edit") ? [brain, workbench] : [])];
      workspace = home ?? ws;
    }
  }
  return { rw, ro, closed, workspace, home, hasShell, holdsBrowser };
}

export function buildAgentPolicies(input: PolicyInput) {
  const agents: Record<string, unknown> = {};
  const view = (def: AgentDef): boolean => def.browserViewOnly || def.id !== "operator";
  for (const def of input.defs) {
    if (def.tools.length === 0) continue;
    const p = perAgent(def, input);
    const exec = p.hasShell
      ? {
          hands: def.id === "mind" || def.id === "worker",
          handsDeny: HANDS_DENY,
          teenyBins: p.home && (def.id === "mind" || def.id === "worker") ? [join(p.home, "bin", "teeny")] : [],
          readOnly: READ_ONLY,
          writers: WRITERS,
          tools: TOOLS,
        }
      : undefined;
    const browser = p.holdsBrowser
      ? view(def)
        ? { actions: VIEW_ACTIONS, actKinds: [], profile: VIEW_PROFILE, keychain: false }
        : { actions: [...VIEW_ACTIONS, "act"], actKinds: UNATTENDED_KINDS, profile: OPERATOR_PROFILE, keychain: true }
      : undefined;
    agents[def.id] = {
      id: def.id,
      tools: toolIds(def.tools),
      workspace: p.workspace,
      rw: p.rw,
      ro: p.ro,
      closed: p.closed,
      // The shell's jail was narrower than the file tools' roots (mind.toml: "its shell writes only its
      // home and reads the checkout's code"): no live brain, no workbench.
      ...(exec ? { execRw: p.home ? [p.home] : [], execRo: [...codeRoots(input.paths.root, input.paths.brainDir), input.workspaceOf(def.id)] } : {}),
      ...(exec ? { exec } : {}),
      ...(browser ? { browser } : {}),
      subagents: def.subagentAllowlist,
      maxToolCalls: def.maxIterations ?? 40,
      ...(def.id === "mind" || def.id === "worker" ? { replyCaps: BEAT_REPLY_CAPS } : {}),
    };
  }
  return agents;
}

/**
 * What one beat of the mind may answer: the inbox pass (brain/agents/mind.md). The worker, which the mind may
 * spawn, holds the same caps for its own run; the hands' daily caps still apply to both.
 */
export const BEAT_REPLY_CAPS = { x: 3, mail: 2 };
/** The reply-pass block put in front of a mind turn (bin/reply-queue): about 1.5k characters. */
export const WAKE_CONTEXT_CHARS = 1500;
export const WAKE_CONTEXT_MS = 8000;

/** [memory.recall] budget_tokens (bin/lib/core_sections/context.py). */
export const RECALL_BUDGET_TOKENS = 3000;
/** [memory] recall_each_turn_ms. */
export const RECALL_EACH_TURN_MS = 5000;

/** Where teeny-guardrails says it is loaded (state/openclaw/guardrails.ready). */
export function guardrailsReadyFile(openclawDir: string): string {
  return join(openclawDir, "guardrails.ready");
}

export function buildGuardrailsPolicy(input: PolicyInput): Record<string, unknown> {
  const { paths, env } = input;
  const deny = join(paths.root, "deploy", "browser-deny.txt");
  const recallAgents: Record<string, { budgetTokens: number }> = {};
  for (const def of input.defs) {
    if (def.omitMemoryContext) continue;
    recallAgents[def.id] = { budgetTokens: Math.min(RECALL_BUDGET_TOKENS, def.memoryBlockMaxTokens ?? RECALL_BUDGET_TOKENS) };
  }
  const home = input.home ?? homedir();
  const pictures = env.TEENY_BROWSER_PICTURES;
  return {
    root: paths.root,
    stateDir: paths.stateDir,
    handsDir: join(paths.root, "hands"),
    home,
    systemForbidden: SYSTEM_FORBIDDEN,
    browserDenyFile: env.OPENHUMAN_BROWSER_DENY_FILE || deny,
    browserAllowFile: join(paths.root, "deploy", "browser-allow.txt"),
    ...(existsSync(join(paths.root, "bin", "keychain-fill")) ? { keychainFill: join(paths.root, "bin", "keychain-fill") } : {}),
    ...(pictures && pictures.startsWith("/") ? { picturesDir: pictures } : {}),
    picturesKeep: 24,
    // The plugin writes this file once its hooks are registered; the supervisor stops a Gateway without it.
    readyFile: guardrailsReadyFile(paths.openclawDir),
    openclaw: openclawCommand(env),
    agents: buildAgentPolicies(input),
    ...(existsSync(join(paths.root, "bin", "reply-queue"))
      ? { wakeContext: { agents: ["mind"], argv: [join(paths.root, "bin", "reply-queue")], cwd: paths.root, maxChars: WAKE_CONTEXT_CHARS, timeoutMs: WAKE_CONTEXT_MS } }
      : {}),
    recall: {
      agents: recallAgents,
      engineFile: join(paths.stateDir, "memory-engine.json"),
      masterKeyFile: env.OPENHUMAN_KEYRING_MASTER_KEY_FILE || join(paths.secretsDir, "openhuman.key"),
      timeoutMs: RECALL_EACH_TURN_MS,
    },
  };
}
