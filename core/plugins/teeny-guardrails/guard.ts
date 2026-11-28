// Every rule on one tool call, in one place, so the hook in index.ts is a thin
// shell and the rules are testable without a Gateway. The plugin can only
// refuse a call or narrow it (an absolute program path, a typed password); it
// never adds a tool, a path, a program or an environment variable.
import { resolve } from "node:path";
import { checkPath, expandHome, type PathRules } from "./paths.js";
import { countReplies, judgeExec } from "./exec.js";
import { judgeAllowedFetch, judgeBrowser, newBrowserState, openclawLiveUrl, runKeychainFill, type BrowserState } from "./browser.js";
import { exitCodeOf, NativeState } from "./native.js";
import type { AgentPolicy, GuardrailsPolicy } from "./policy.js";

export interface ToolEvent {
  toolName: string;
  params: Record<string, unknown>;
  runId?: string;
}
export interface ToolContext {
  agentId?: string;
  sessionKey?: string;
  runId?: string;
}
export type Decision = { block: true; blockReason: string } | { params: Record<string, unknown> } | undefined;

const PATH_KEYS = ["path", "file_path", "filePath", "file", "filename", "paths"];
/** Tools that never carry a path or a command and cannot reach outside the agent's own session. */
const PASSIVE = new Set(["tool_search", "memory_search", "memory_get", "process"]);
/** The tool-search controls, which are never themselves the target of a dispatch. */
const CONTROLS = new Set(["tool_search", "tool_describe", "dispatch_action"]);
const MAX_RUNS_KEPT = 4096;

/**
 * The memory tools teeny-cortexdb registers (agents/definitions.ts MEMORY_TOOLS): the keys each takes. A call
 * with any other key is refused, so an argument that asks for more than the tool offers (a scope, an `all`, a
 * `confirmAll`) never reaches it. memory_forget also must name what to forget and why.
 */
const MEMORY_PARAMS: Record<string, readonly string[]> = {
  memory_recall: ["query", "limit"],
  memory_list: ["limit", "cursor"],
  memory_store: ["content", "kind"],
  memory_forget: ["query", "reason"],
};
const MEMORY_TEXT_MAX = 4000;

function judgeMemory(tool: string, params: Record<string, unknown>): string | undefined {
  const allowed = MEMORY_PARAMS[tool] as readonly string[];
  for (const key of Object.keys(params)) if (!allowed.includes(key)) return `${tool} takes ${allowed.join(", ")} and nothing else (not ${key})`;
  const text = (key: string, min: number): string | undefined => {
    const v = params[key];
    if (typeof v !== "string" || v.trim().length < min) return `${tool}: ${key} must be text of at least ${min} characters`;
    if (v.length > MEMORY_TEXT_MAX) return `${tool}: ${key} is longer than ${MEMORY_TEXT_MAX} characters`;
    return undefined;
  };
  switch (tool) {
    case "memory_recall":
      return text("query", 1);
    case "memory_store":
      return text("content", 1);
    case "memory_forget":
      return text("query", 3) ?? text("reason", 8);
    default:
      return undefined;
  }
}

const refuse = (why: string): Decision => ({ block: true, blockReason: `teeny-guardrails: ${why}` });

export interface GuardDeps {
  fill?: (program: string, host: string) => Promise<string>;
  liveUrl?: (profile: string, targetId: string | undefined) => Promise<string>;
  /** The clock, for the native playbooks' UTC day. */
  now?: () => number;
}

export class Guardrails {
  private readonly runs = new Map<string, number>();
  /** Replies sent so far by each run (the same key as `runs`), against AgentPolicy.replyCaps. */
  private readonly replies = new Map<string, { x: number; mail: number }>();
  /** The acting-hand calls a judged exec call holds, by the params object judge returned for it. */
  private readonly actingIn = new WeakMap<Record<string, unknown>, { calls: number; last: boolean }>();
  readonly browserState: BrowserState = newBrowserState();
  /** The day's count, the lock and the run log of the native playbooks (native.ts). */
  readonly native: NativeState;

  constructor(
    readonly policy: GuardrailsPolicy,
    private readonly deps: GuardDeps = {},
  ) {
    this.native = new NativeState(policy.stateDir, deps.now);
  }

  private runKeyOf(agent: AgentPolicy, ctx: ToolContext, event?: ToolEvent): string {
    return `${agent.id}|${ctx.runId ?? event?.runId ?? ctx.sessionKey ?? ""}`;
  }

  /** after_tool_call: a native hand's refusal (exit 2) gives the day's reservation back. Observation only. */
  observe(event: { toolName: string; params: Record<string, unknown>; result?: unknown; runId?: string }, ctx: ToolContext): void {
    const agent = ctx.agentId ? this.policy.agents[ctx.agentId] : undefined;
    const n = agent?.exec?.native;
    if (!agent || !n || event.toolName.toLowerCase() !== "exec" || typeof event.params.command !== "string") return;
    this.native.settle(n, this.runKeyOf(agent, ctx, event), event.params.command, exitCodeOf(event.result));
  }

  /** agent_end: the run is over; its lock is released and its record written. */
  runEnded(event: { runId?: string; success: boolean; error?: string }, ctx: ToolContext): void {
    const agent = ctx.agentId ? this.policy.agents[ctx.agentId] : undefined;
    if (!agent?.exec?.native) return;
    this.native.end(this.runKeyOf(agent, { ...ctx, ...(event.runId ? { runId: event.runId } : {}) }), { ok: event.success, ...(event.error ? { error: event.error } : {}) });
  }

  /** Counts a tool call against its run; true when the run has spent its budget. */
  private spent(agent: AgentPolicy, key: string): { used: number; over: boolean } {
    const used = (this.runs.get(key) ?? 0) + 1;
    this.runs.delete(key);
    this.runs.set(key, used);
    if (this.runs.size > MAX_RUNS_KEPT) {
      const oldest = this.runs.keys().next().value;
      if (oldest !== undefined) this.runs.delete(oldest);
    }
    return { used, over: used > agent.maxToolCalls };
  }

  private rules(agent: AgentPolicy): PathRules {
    return { workspace: agent.workspace, rw: agent.rw, ro: agent.ro, closed: agent.closed, systemForbidden: this.policy.systemForbidden, home: this.policy.home };
  }

  async beforeToolCall(event: ToolEvent, ctx: ToolContext): Promise<Decision> {
    const agent = ctx.agentId ? this.policy.agents[ctx.agentId] : undefined;
    if (!agent) return refuse(`agent ${ctx.agentId ?? "(unknown)"} holds no tools`);
    const tool = event.toolName.toLowerCase();
    const runKey = this.runKeyOf(agent, ctx, event);
    const budget = this.spent(agent, runKey);
    if (budget.over) return refuse(`this run has made its ${agent.maxToolCalls} tool calls (the budget for ${agent.id}); end the run`);
    // A native playbook runs one at a time: the first call of a run takes its lock (the same one bin/act takes).
    const native = agent.exec?.native;
    if (native) {
      const held = this.native.begin(native, runKey);
      if (held) return refuse(held);
    }
    const decision = await this.judge(agent, ctx, tool, event.toolName, event.params ?? {}, runKey);
    // The acting hand is counted here, before it runs, and only for a call that is otherwise allowed.
    if (native && decision && "params" in decision && tool === "exec") {
      const acting = this.actingIn.get(decision.params);
      if (acting) {
        const why = this.native.reserve(native, runKey, acting.calls, String(decision.params.command), acting.last);
        if (why) return refuse(why);
      }
    }
    return decision;
  }

  /**
   * The per-run reply caps: a run (one beat of the mind) that has already sent its allowance of replies on a
   * channel is refused the next. Counted when the call is made, as the hands' own daily caps are, so a reply the
   * hand then refuses still uses one: a beat cannot probe its way past the count. Another run, or another agent,
   * has its own count.
   */
  private repliesSpent(agent: AgentPolicy, runKey: string, command: string, cwd: string): string | undefined {
    const caps = agent.replyCaps;
    if (!caps) return undefined;
    const asked = countReplies(command, cwd);
    const channels = ["x", "mail"] as const;
    if (channels.every((c) => asked[c] === 0)) return undefined;
    const sent = this.replies.get(runKey) ?? { x: 0, mail: 0 };
    for (const c of channels) {
      const cap = caps[c];
      if (cap !== undefined && asked[c] > 0 && sent[c] + asked[c] > cap) {
        return `this run has already sent its ${cap} ${c === "x" ? "X" : "mail"} repl${cap === 1 ? "y" : "ies"} (the most one beat of ${agent.id} answers); leave the rest for the next beat`;
      }
    }
    this.replies.delete(runKey);
    this.replies.set(runKey, { x: sent.x + asked.x, mail: sent.mail + asked.mail });
    if (this.replies.size > MAX_RUNS_KEPT) {
      const oldest = this.replies.keys().next().value;
      if (oldest !== undefined) this.replies.delete(oldest);
    }
    return undefined;
  }

  /** One tool call against one agent's own list; the budget is already spent. */
  private async judge(agent: AgentPolicy, ctx: ToolContext, tool: string, shown: string, params: Record<string, unknown>, runKey: string): Promise<Decision> {
    const event = { toolName: shown, params };
    const holds = (name: string): boolean => agent.tools.some((t) => t.toLowerCase() === name);

    // OpenClaw offers tool_describe and dispatch_action to every agent whatever its tools.deny says. They
    // belong to the tool_search tool: an agent without it holds neither, and dispatch_action is judged as the
    // tool it names, with the arguments it passes, so it can reach nothing the agent's own list does not.
    if (tool === "tool_describe" || tool === "dispatch_action") {
      if (!holds("tool_search")) return refuse(`${agent.id} has no ${shown} tool`);
      if (tool === "tool_describe") return undefined;
      const id = params.id;
      const target = typeof id === "string" ? id.toLowerCase() : "";
      if (!target || CONTROLS.has(target)) return refuse(`dispatch_action: ${typeof id === "string" && id ? id : "(no id)"} is not a tool it may run`);
      if (!holds(target)) return refuse(`dispatch_action: ${agent.id} has no ${id as string} tool`);
      const args = params.args;
      if (args !== undefined && (typeof args !== "object" || args === null || Array.isArray(args))) return refuse("dispatch_action: args is not an object");
      const inner = await this.judge(agent, ctx, target, id as string, (args ?? {}) as Record<string, unknown>, runKey);
      if (inner && "params" in inner) return { params: { ...params, args: inner.params } };
      return inner;
    }
    if (!holds(tool)) return refuse(`${agent.id} has no ${shown} tool`);

    if (PASSIVE.has(tool)) return undefined;

    switch (tool) {
      case "read":
      case "write":
      case "edit": {
        const access = tool === "read" ? "read" : "write";
        const raws: unknown[] = [];
        for (const key of PATH_KEYS) {
          const v = params[key];
          if (Array.isArray(v)) raws.push(...v);
          else if (v !== undefined) raws.push(v);
        }
        if (raws.length === 0) return refuse(`${tool} names no path I can check`);
        for (const raw of raws) {
          const why = checkPath(this.rules(agent), raw, access);
          if (why) return refuse(`${tool}: ${why}`);
        }
        // Run the tool on the absolute path that was judged, whatever its own working folder is.
        const absolute = (raw: unknown): unknown => (typeof raw === "string" ? resolve(agent.workspace, expandHome(raw, this.policy.home)) : raw);
        const fixed: Record<string, unknown> = { ...params };
        for (const key of PATH_KEYS) {
          const v = params[key];
          if (Array.isArray(v)) fixed[key] = v.map(absolute);
          else if (v !== undefined) fixed[key] = absolute(v);
        }
        return { params: fixed };
      }
      case "exec": {
        const verdict = judgeExec(this.policy, agent, params);
        if (!verdict.ok) return refuse(`exec: ${verdict.reason}`);
        const over = this.repliesSpent(agent, runKey, params.command as string, verdict.cwd);
        if (over) return refuse(`exec: ${over}`);
        const next = { ...params, command: verdict.command, workdir: verdict.cwd };
        if (verdict.acting > 0) this.actingIn.set(next, { calls: verdict.acting, last: verdict.actingLast });
        return { params: next };
      }
      case "browser": {
        if (!agent.browser) return refuse("this agent has no browser");
        const v = await judgeBrowser(
          agent.browser,
          { denyFile: this.policy.browserDenyFile, keychainFill: this.policy.keychainFill, liveUrl: this.deps.liveUrl ?? (this.policy.openclaw ? openclawLiveUrl(this.policy.openclaw) : async () => { throw new Error("no way to ask the browser"); }), fill: this.deps.fill ?? runKeychainFill },
          this.browserState,
          ctx.sessionKey,
          params,
        );
        if (v && "block" in v) return refuse(`browser: ${v.block}`);
        const next = v?.params ?? params;
        // A named profile keeps the mind's throwaway browser apart from the one signed in as Teeny.
        const profile = agent.browser.profile;
        if (profile && next.profile !== profile) return { params: { ...next, profile } };
        return v?.params ? { params: v.params } : undefined;
      }
      case "web_fetch":
      case "http_request": {
        const v = judgeAllowedFetch(params.url, this.policy.browserAllowFile);
        return v.ok ? undefined : refuse(`${tool}: ${v.reason}`);
      }
      case "sessions_spawn": {
        const id = params.agentId;
        if (typeof id !== "string" || !agent.subagents.includes(id)) {
          return refuse(`sessions_spawn: ${agent.id} may spawn only ${agent.subagents.join(", ") || "nobody"}`);
        }
        if (params.runtime !== undefined && params.runtime !== "subagent") return refuse("sessions_spawn: only subagent runs are allowed");
        for (const k of ["model", "cwd", "projectGitUrl", "projectId", "resumeSessionId", "cyberAccessProgram"]) {
          if (params[k] !== undefined) return refuse(`sessions_spawn: ${k} may not be set`);
        }
        if (params.sandbox === "inherit" || params.sandbox === undefined || params.sandbox === "require") return undefined;
        return refuse("sessions_spawn: sandbox may only inherit or require");
      }
      case "subagents":
        return agent.subagents.length > 0 ? undefined : refuse("this agent has no helpers");
      case "memory_recall":
      case "memory_list":
      case "memory_store":
      case "memory_forget": {
        const why = judgeMemory(tool, params);
        return why ? refuse(why) : undefined;
      }
      default:
        return refuse(`${event.toolName} is not a tool this guard knows how to judge`);
    }
  }
}
