// Every rule on one tool call, in one place, so the hook in index.ts is a thin
// shell and the rules are testable without a Gateway. The plugin can only
// refuse a call or narrow it (an absolute program path, a typed password); it
// never adds a tool, a path, a program or an environment variable.
import { resolve } from "node:path";
import { checkPath, expandHome, type PathRules } from "./paths.js";
import { judgeExec } from "./exec.js";
import { judgeAllowedFetch, judgeBrowser, newBrowserState, runKeychainFill, type BrowserState } from "./browser.js";
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
const MAX_RUNS_KEPT = 4096;

const refuse = (why: string): Decision => ({ block: true, blockReason: `teeny-guardrails: ${why}` });

export interface GuardDeps {
  fill?: (program: string, host: string) => Promise<string>;
}

export class Guardrails {
  private readonly runs = new Map<string, number>();
  readonly browserState: BrowserState = newBrowserState();

  constructor(
    readonly policy: GuardrailsPolicy,
    private readonly deps: GuardDeps = {},
  ) {}

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
    const runKey = `${agent.id}|${ctx.runId ?? event.runId ?? ctx.sessionKey ?? ""}`;
    const budget = this.spent(agent, runKey);
    if (budget.over) return refuse(`this run has made its ${agent.maxToolCalls} tool calls (the budget for ${agent.id}); end the run`);
    if (!agent.tools.map((t) => t.toLowerCase()).includes(tool)) return refuse(`${agent.id} has no ${event.toolName} tool`);
    const params = event.params ?? {};

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
        return { params: { ...params, command: verdict.command, workdir: verdict.cwd } };
      }
      case "browser": {
        if (!agent.browser) return refuse("this agent has no browser");
        const v = await judgeBrowser(
          agent.browser,
          { denyFile: this.policy.browserDenyFile, keychainFill: this.policy.keychainFill, fill: this.deps.fill ?? runKeychainFill },
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
      default:
        return refuse(`${event.toolName} is not a tool this guard knows how to judge`);
    }
  }
}
