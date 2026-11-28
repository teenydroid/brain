// A scratch checkout and the policy the core would generate for it, from the
// real brain/agents/*.toml, so the rules are tested on Teeny's own agents.
import { chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildGuardrailsPolicy } from "../../src/agents/guardrails.js";
import { loadAgentDefs } from "../../src/agents/definitions.js";
import { loadNativeDefs } from "../../src/agents/native.js";
import { Guardrails, type Decision } from "../../plugins/teeny-guardrails/guard.js";
import { parsePolicy, type AgentPolicy, type GuardrailsPolicy } from "../../plugins/teeny-guardrails/policy.js";
import { afterEach } from "vitest";
import { REPO } from "../helpers.js";

// Every checkout a test makes is removed after that test, whether it passed or not.
const made: string[] = [];
afterEach(() => {
  for (const dir of made.splice(0)) rmSync(dir, { recursive: true, force: true });
});

export interface Fixture {
  root: string;
  paths: { root: string; brainDir: string; stateDir: string; secretsDir: string; openclawDir: string };
  policy: GuardrailsPolicy;
  guard: Guardrails;
  agent: (id: string) => AgentPolicy;
  home: string;
  call: (agent: string, tool: string, params: Record<string, unknown>, ctx?: { runId?: string }) => Promise<Decision>;
}

export function write(path: string, text = "x\n", mode?: number): void {
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, text);
  if (mode !== undefined) chmodSync(path, mode);
}

export function makeFixture(extra: { now?: () => number; memoryTools?: boolean; env?: Record<string, string>; fill?: (program: string, host: string) => Promise<string>; liveUrl?: (profile: string, targetId: string | undefined) => Promise<string> } = {}): Fixture {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "guard-")));
  made.push(root);
  const brain = join(root, "brain");
  const state = join(root, "state");
  // The native playbooks' hands (gm, moltbook-general) exist too, so their inputs and acting hands are real files.
  for (const hand of ["x", "mail", "gh", "wallet", "keychain", "stats", "listen", "hn", "story", "moltbook", "image", "video", "tasks"]) write(join(root, "hands", hand), "#!/bin/sh\necho ok\n", 0o755);
  write(join(root, "hands", "README.md"));
  write(join(root, "hands", "lib", "mod.py"));
  write(join(root, "hands", "not-exec"), "text", 0o644);
  write(join(root, "bin", "doctor"), "#!/bin/sh\n", 0o755);
  write(join(root, "docs", "hands.md"), "docs\n");
  write(join(root, "secrets", "teeny.env"), "TOKEN=fake\n");
  write(join(root, ".git", "config"), "[core]\n");
  write(join(root, ".openhuman", "config.toml"), "k=1\n");
  write(join(brain, "SOUL.md"), "soul\n");
  write(join(brain, "MEMORY.md"), "memory\n");
  write(join(brain, "library", "note.md"), "note\n");
  write(join(brain, "playbooks", "p.md"), "p\n");
  write(join(brain, "agents", "mind.md"), "m\n");
  write(join(state, "home", "bin", "teeny"), "#!/bin/sh\necho broker\n", 0o755);
  write(join(state, "home", "work", "a.txt"), "a\n");
  write(join(state, "sitter", "home", "pr.json"), "{}\n");
  write(join(state, "workbench", "desk", "task.json"), "{}\n");
  write(join(state, "workbench", "other.txt"), "o\n");
  write(join(state, "ledger", "wallet.json"), "{}\n");
  write(join(state, "openclaw", "openclaw.json"), "{}\n");
  mkdirSync(join(state, "openclaw", "workspaces"), { recursive: true });
  write(join(root, "deploy", "browser-deny.txt"), "# never\nx.com\ntwitter.com\n");
  write(join(root, "deploy", "browser-allow.txt"), "api.example.org\n");
  write(join(root, "bin", "keychain-fill"), "#!/bin/sh\n", 0o755);
  const paths = { root, brainDir: brain, stateDir: state, secretsDir: join(root, "secrets"), openclawDir: join(state, "openclaw") };
  // The agents of the shipped native playbooks (pb-gm, pb-moltbook-general) are built from the real playbooks.
  const defs = [...loadAgentDefs(join(REPO, "brain")), ...loadNativeDefs(join(REPO, "brain"), REPO).defs];
  const raw = buildGuardrailsPolicy({
    defs,
    paths,
    env: { TEENY_BROWSER_PICTURES: join(state, "browser", "screenshots"), ...(extra.env ?? {}) },
    workspaceOf: (id) => join(state, "openclaw", "workspaces", id),
    home: "/nonexistent-home",
    ...(extra.memoryTools ? { memoryTools: true } : {}),
  });
  const policy = parsePolicy({ policy: raw });
  const guard = new Guardrails(policy, { ...(extra.now ? { now: extra.now } : {}), ...(extra.fill ? { fill: extra.fill } : {}), liveUrl: extra.liveUrl ?? (async () => "https://example.org/") });
  const agent = (id: string): AgentPolicy => {
    const a = policy.agents[id];
    if (!a) throw new Error(`no policy for ${id}`);
    return a;
  };
  return {
    root,
    paths,
    policy,
    guard,
    agent,
    home: join(state, "home"),
    call: (id, tool, params, ctx = {}) => guard.beforeToolCall({ toolName: tool, params }, { agentId: id, sessionKey: `agent:${id}:t`, ...ctx }),
  };
}

export const link = (target: string, path: string): void => {
  mkdirSync(join(path, ".."), { recursive: true });
  symlinkSync(target, path);
};

export function blocked(d: Decision): string | undefined {
  return d && "block" in d ? d.blockReason : undefined;
}
export function rewritten(d: Decision): Record<string, unknown> | undefined {
  return d && "params" in d ? d.params : undefined;
}
