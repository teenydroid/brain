// The exec allowlist. OpenHuman gated the shell by `allowed_commands` and by
// refusing hidden-command syntax; this reads the command (shell.ts), then
// allows each program only if it is
//   - a hand: hands/<name>, by repo-relative or absolute path, an executable
//     file directly in the repo's hands/ folder, not on this agent's deny list;
//   - the broker client this agent was given (bin/teeny in its home);
//   - a plain program name on this agent's list (read-only, writers, tools).
// A path to anything else, however spelled, is refused: the program is never
// looked up on PATH unless its name is on the list.
import { statSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { checkPath, expandHome, realish, type PathRules } from "./paths.js";
import { parseCommand, shellQuote, type Segment, type Word } from "./shell.js";
import type { AgentPolicy, GuardrailsPolicy } from "./policy.js";

export type ExecVerdict = { ok: true; command: string; cwd: string } | { ok: false; reason: string };

/** In hands/ but not hands. */
const NOT_HANDS = new Set(["README.md", "lib", "tests"]);

/** Hands that move money, hold keys or sign in, which no agent runs through its shell (agent_check.py NEVER, no-model-pays). */
export const DEFAULT_HANDS_DENY = ["wallet", "x402", "giveaway", "bounty", "rent", "slopcash", "surplus", "keychain", "vps"];

/** OpenHuman's default read-only inspection commands (config/schema/autonomy.rs), plus jq and test. */
export const READ_ONLY = [
  "ls", "cat", "grep", "find", "echo", "pwd", "wc", "head", "tail", "date", "sort", "uniq", "diff", "which", "uname",
  "basename", "dirname", "tr", "cut", "realpath", "readlink", "stat", "file", "jq",
];
/** Its filesystem-mutating commands. */
export const WRITERS = ["mkdir", "touch", "cp", "mv", "ln"];
/** Its version-control, package and build commands. */
export const TOOLS = ["git", "npm", "pnpm", "yarn", "cargo", "make", "cmake", "timeout"];

const FIND_DENIED = ["-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"];

function isFlag(w: Word): boolean {
  return !w.quoted && w.text.startsWith("-") && w.text.length > 1;
}

function pathRules(policy: GuardrailsPolicy, agent: AgentPolicy, cwd: string): PathRules {
  return {
    workspace: cwd,
    rw: agent.execRw ?? agent.rw,
    ro: agent.execRo ?? agent.ro,
    closed: agent.closed,
    systemForbidden: policy.systemForbidden,
    home: policy.home,
  };
}

function isFileOrExec(path: string): boolean {
  try {
    const st = statSync(path);
    return st.isFile() && (st.mode & 0o111) !== 0;
  } catch {
    return false;
  }
}

/**
 * Resolves a program word that has a slash to an allowed hand or broker client.
 * Returns the absolute path to run, or a refusal.
 */
function resolveProgramPath(
  policy: GuardrailsPolicy,
  agent: AgentPolicy,
  word: Word,
  cwd: string,
): { path: string } | { reason: string } {
  const exec = agent.exec;
  if (!exec) return { reason: "this agent has no shell" };
  const text = word.text;
  if (word.glob || text.includes("\u0001")) return { reason: "the program name is not a plain path" };
  // hands/<name> is repo-relative, whatever the working folder is.
  const candidate = /^hands\//.test(text)
    ? join(policy.root, text)
    : resolve(cwd, expandHome(text, policy.home));
  let real: string;
  try {
    real = realish(candidate);
  } catch {
    return { reason: `${text} cannot be resolved` };
  }
  const handsDir = realish(policy.handsDir);
  if (exec.hands && dirname(real) === handsDir && dirname(resolve(candidate)) === resolve(policy.handsDir)) {
    const name = basename(real);
    if (basename(resolve(candidate)) !== name) return { reason: `${text} is a link to another file, not a hand` };
    if (NOT_HANDS.has(name) || name.startsWith(".")) return { reason: `${text} is not a hand` };
    if (exec.handsDeny.includes(name)) return { reason: `hands/${name} is closed to this agent (it moves money or holds keys)` };
    if (!isFileOrExec(real)) return { reason: `hands/${name} is not an executable hand` };
    return { path: join(policy.handsDir, name) };
  }
  for (const bin of exec.teenyBins) {
    const wanted = realish(bin);
    if (real === wanted && resolve(candidate) === resolve(bin)) return { path: bin };
  }
  return {
    reason: exec.hands
      ? `${text} is not an allowed program: run a hand as hands/<name>, the broker as bin/teeny, or one of the listed commands`
      : `${text} is not an allowed program for this agent`,
  };
}

function checkArgPaths(
  rules: PathRules,
  words: Word[],
  mode: "read" | "write" | "mixed",
  program: string,
): string | undefined {
  const positional: Word[] = [];
  for (const w of words) {
    // The shell would expand these before the program sees them; a path judged as written is not the path used.
    if (w.text.includes("\u0001")) return `${program}: "$(cat ...)" is only for the arguments of a hand or the broker, where it is data`;
    if (w.glob) return `${program}: quote the glob ${w.text}: an unquoted * ? [ is expanded by the shell after it was judged`;
    if (w.text.startsWith("~")) return `${program}: ~ is expanded by the shell, not by this guard: write the path out`;
    if (isFlag(w)) {
      const eq = w.text.indexOf("=");
      if (w.text.startsWith("--") && eq > 0) {
        const value = w.text.slice(eq + 1);
        if (value.startsWith("~")) return `${program}: ~ is expanded by the shell, not by this guard: write the path out`;
        if (value.includes("/") || value.includes("..")) {
          const why = checkPath(rules, value, mode === "write" ? "write" : "read");
          if (why) return `${program}: ${why}`;
        }
      } else if (!w.text.startsWith("--") && w.text.includes("/")) {
        return `${program}: the option ${w.text} carries a path; pass paths as separate words`;
      }
      continue;
    }
    positional.push(w);
  }
  for (let i = 0; i < positional.length; i++) {
    const w = positional[i] as Word;
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(w.text)) {
      if (/^file:/i.test(w.text)) return `${program}: a file: URL is a path in disguise`;
      continue; // a URL, not a path
    }
    let access: "read" | "write" = "read";
    if (mode === "write") access = "write";
    else if (mode === "mixed") access = i === positional.length - 1 ? "write" : "read";
    const why = checkPath(rules, w.text, access);
    if (why) return `${program}: ${why}`;
  }
  return undefined;
}

/** git subcommands an agent may run. Everything else (config, submodule, bisect, daemon, worktree, hook ...) is refused by default. */
const GIT_SUBCOMMANDS = new Set([
  "status", "log", "diff", "show", "add", "commit", "branch", "checkout", "switch", "restore", "clone", "fetch", "pull", "push", "init",
  "remote", "rev-parse", "rev-list", "ls-files", "ls-tree", "ls-remote", "blame", "merge", "rebase", "stash", "tag", "apply", "cherry-pick",
  "reset", "mv", "rm", "describe", "shortlog", "grep", "show-ref", "cat-file", "diff-tree", "name-rev", "clean", "revert", "version", "help",
]);
/** The ones that change files, so their path arguments must be writable. */
const GIT_WRITES = new Set(["add", "checkout", "switch", "restore", "clone", "init", "mv", "rm", "apply", "reset", "clean", "stash", "revert", "cherry-pick", "merge", "rebase", "pull", "commit"]);
/** Options anywhere after the subcommand that run a program or write where they are told. */
const GIT_DENIED_OPTS = [
  "--upload-pack", "--receive-pack", "--exec", "--exec-path", "--ext-diff", "--textconv", "--open-files-in-pager", "--output", "--output-directory",
  "--template", "--config", "--config-env", "--git-dir", "--work-tree", "--paginate", "--pager", "--run", "--sshCommand", "--recurse-submodules",
  "--ext-cmd", "--separate-git-dir", "--bundle-uri",
];

/**
 * Reads git's own global options the way git does (so `-C dir` cannot hide the subcommand), allows only
 * `-C dir` and the harmless switches, then judges the subcommand and its options.
 */
function judgeGit(rules: PathRules, args: Word[], cwd: string): string | undefined {
  let i = 0;
  let dir = cwd;
  for (; i < args.length; i++) {
    const a = (args[i] as Word).text;
    if (a === "-C") {
      const d = args[i + 1];
      if (!d) return "git -C needs a folder";
      const w = checkArgPaths(rules, [d], "write", "git -C");
      if (w) return w;
      dir = d.text;
      i++;
      continue;
    }
    if (["--no-pager", "-P", "--no-replace-objects", "--literal-pathspecs", "--no-optional-locks", "--bare"].includes(a)) continue;
    if (a.startsWith("-")) return `git ${a.split("=")[0]} before the subcommand can run a program or change what git trusts`;
    break;
  }
  const sub = args[i]?.text;
  if (!sub) return "git needs a subcommand";
  if (!GIT_SUBCOMMANDS.has(sub)) return `git ${sub} is not on the list of git subcommands this agent may run`;
  const rest = args.slice(i + 1);
  for (const w of rest) {
    const t = w.text;
    if (/^[A-Za-z0-9+.-]+::/.test(t) || /^ext:/i.test(t) || /^fd:/i.test(t)) return "git remote helpers (ext::, fd::, x::y) run programs";
    if (!isFlag(w)) continue;
    const name = t.split("=")[0] as string;
    if (GIT_DENIED_OPTS.some((o) => name === o || (o.length > 2 && name.startsWith(`${o}=`)))) return `git ${sub} ${name} can run a program or write where it is told`;
    if (/^-O/.test(t) || name === "-x" || name === "-o" || name === "--exec") return `git ${sub} ${name} can run a program or write where it is told`;
    if (name === "-u" && ["clone", "ls-remote", "fetch", "pull"].includes(sub)) return `git ${sub} -u runs a program (upload-pack)`;
  }
  // After -C, relative paths start in that folder.
  return checkArgPaths({ ...rules, workspace: resolve(cwd, dir) }, rest, GIT_WRITES.has(sub) ? "write" : "read", `git ${sub}`);
}

function checkSegment(policy: GuardrailsPolicy, agent: AgentPolicy, seg: Segment, cwd: string): { rewrites: { start: number; end: number; to: string }[] } | { reason: string } {
  const exec = agent.exec as NonNullable<AgentPolicy["exec"]>;
  const rules = pathRules(policy, agent, cwd);
  const rewrites: { start: number; end: number; to: string }[] = [];
  let words = seg.words;
  const first = words[0] as Word;
  if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(first.text) && !first.quoted) {
    return { reason: "a VAR=value prefix could change what a program does (env, PATH, DRY_RUN): set nothing, run the program" };
  }
  // `timeout N prog ...`: the wrapped program is judged as if run alone. Unwrapped in a loop, so
  // `timeout 5 timeout 5 rm x` is judged as `rm x`, never as an allowed `timeout`.
  while (words[0] && words[0].text === "timeout" && !words[0].quoted && (exec.readOnly.includes("timeout") || exec.tools.includes("timeout") || exec.writers.includes("timeout"))) {
    let k = 1;
    while (k < words.length && isFlag(words[k] as Word)) k++;
    if (k >= words.length || !/^[0-9]+(\.[0-9]+)?[smhd]?$/.test((words[k] as Word).text)) return { reason: "timeout needs a duration then a program" };
    words = words.slice(k + 1);
    if (words.length === 0) return { reason: "timeout has no program" };
  }
  const prog = words[0] as Word;
  const args = words.slice(1);

  // Builtins that only set limits are fine.
  if (prog.text === "ulimit" && !prog.quoted) {
    if (args.every((a) => /^-[a-zA-Z]$|^[0-9]+$|^unlimited$/.test(a.text))) return { rewrites };
    return { reason: "ulimit takes only flags and numbers" };
  }

  if (prog.text.includes("/")) {
    if (prog.quoted) return { reason: "write the program path without quotes or backslashes" };
    const resolved = resolveProgramPath(policy, agent, prog, cwd);
    if ("reason" in resolved) return resolved;
    // Run exactly the file that was judged, whatever the shell's working folder is.
    rewrites.push({ start: prog.start, end: prog.start + prog.text.length, to: shellQuote(resolved.path) });
    // A hand or the broker client takes its own words; they own their rules (the guard, caps, DRY_RUN).
    for (const w of args) {
      if (w.glob) return { reason: "a glob in a hand's arguments could name a file the hand was not meant to read" };
    }
    return { rewrites };
  }

  // A plain name: must be on this agent's list.
  const name = prog.text;
  if (prog.glob || prog.text.includes("\u0001")) return { reason: "the program name is not a plain word" };
  const isRead = exec.readOnly.includes(name);
  const isWriter = exec.writers.includes(name);
  const isTool = exec.tools.includes(name);
  if (!isRead && !isWriter && !isTool) {
    return {
      reason: exec.hands
        ? `${name} is not on this agent's command list. Hands run as hands/<name>; the broker as bin/teeny`
        : `${name} is not on this agent's command list`,
    };
  }
  // Whatever the program, no word of it may be a glob or a substituted word the shell will expand after this check.
  for (const a of args) {
    if (a.glob) return { reason: `quote the glob ${a.text}: an unquoted * ? [ is expanded by the shell after it was judged` };
    if (a.text.includes("\u0001")) return { reason: `"$(cat ...)" is only for the arguments of a hand or the broker, where it is data` };
  }
  if (name === "find") {
    for (const a of args) if (FIND_DENIED.includes(a.text)) return { reason: `find ${a.text} can run or write files` };
    const paths: Word[] = [];
    for (const a of args) {
      if (a.text.startsWith("-") || a.text === "(" || a.text === "!") break;
      paths.push(a);
    }
    const why = checkArgPaths(rules, paths, "read", name);
    if (why) return { reason: why };
    return { rewrites };
  }
  if (name === "sort") {
    for (const a of args) {
      if (!isFlag(a)) continue;
      if (/^-[a-zA-Z]*o|^--output/.test(a.text)) return { reason: "sort -o writes a file" };
      if (a.text.startsWith("--compress-program")) return { reason: "sort --compress-program runs a program" };
      if (/^-[a-zA-Z]*T|^--temporary-directory/.test(a.text)) return { reason: "sort -T writes temporary files where it is told" };
    }
  }
  if (name === "jq") {
    // jq reads its own environment ($ENV, env) and imports modules from disk: the gateway's environment holds tokens.
    for (const a of args) {
      if (isFlag(a) && /^(-L|--library-path|--from-file|-f)/.test(a.text)) return { reason: "jq -f and -L read programs and modules from disk" };
      if (/env|ENV|include|import|input_filename|\$__prog/.test(a.text)) return { reason: "jq may not read the environment or import modules" };
    }
  }
  if (name === "cp") {
    for (const a of args) if (isFlag(a) && /^(--link|--symbolic-link|-[a-zA-Z]*[ls])/.test(a.text) && !a.text.startsWith("--no")) return { reason: "cp -l and -s make a second name for a file, which a write could reach through" };
  }
  if (name === "ln") {
    // A hard link to a read-only code file would be a writable name for it.
    if (!args.some((a) => isFlag(a) && /^(--symbolic|-[a-zA-Z]*s)/.test(a.text))) return { reason: "ln without -s makes a hard link to a file the agent may only read" };
  }
  if (name === "tail") {
    for (const a of args) if (/^-[a-zA-Z]*[fF]|^--follow/.test(a.text) && isFlag(a)) return { reason: "tail -f never ends" };
  }
  if (name === "uniq") {
    if (args.filter((a) => !isFlag(a)).length > 1) return { reason: "uniq with two files writes the second" };
  }
  if (name === "date") {
    for (const a of args) if (/^-s|^--set/.test(a.text) && isFlag(a)) return { reason: "date -s sets the clock" };
  }
  if (isTool) {
    if (name === "git") {
      const why = judgeGit(rules, args, cwd);
      if (why) return { reason: why };
      return { rewrites };
    }
    const why = checkArgPaths(rules, args, "read", name);
    if (why) return { reason: why };
    // The tools write where they run: that folder must be writable.
    const cwdWhy = checkPath(rules, cwd, "write");
    if (cwdWhy) return { reason: `${name} writes where it runs, and ${cwdWhy}` };
    return { rewrites };
  }
  if (isWriter) {
    if (name === "cp" || name === "mv" || name === "ln") {
      for (const a of args) if (isFlag(a) && /^(-[a-zA-Z]*t|--target-directory)/.test(a.text)) return { reason: `${name} -t hides its destination` };
    }
    const why = checkArgPaths(rules, args, name === "cp" || name === "ln" ? "mixed" : "write", name);
    if (why) return { reason: why };
    return { rewrites };
  }
  const why = checkArgPaths(rules, args, "read", name);
  if (why) return { reason: why };
  return { rewrites };
}

/** Judges one exec tool call. Returns the command to run (hands/<name> made absolute) or a refusal. */
export function judgeExec(policy: GuardrailsPolicy, agent: AgentPolicy, params: Record<string, unknown>): ExecVerdict {
  const exec = agent.exec;
  if (!exec) return { ok: false, reason: "this agent has no shell" };
  const command = params.command;
  if (typeof command !== "string") return { ok: false, reason: "exec needs a command string" };

  // Nothing in the call may widen the environment or the host the command runs on.
  if (params.env !== undefined && !(typeof params.env === "object" && params.env !== null && Object.keys(params.env).length === 0)) {
    return { ok: false, reason: "exec may not set environment variables (DRY_RUN, the kill switches and PATH pass through unchanged)" };
  }
  if (params.elevated) return { ok: false, reason: "elevated exec is never allowed" };
  if (params.host !== undefined && params.host !== "auto" && params.host !== "gateway") return { ok: false, reason: "exec runs on the gateway host only" };
  if (params.node !== undefined && params.node !== "") return { ok: false, reason: "exec may not target a node" };
  if (params.durable) return { ok: false, reason: "a durable process outlives the run and is not allowed" };

  let cwd = agent.workspace;
  if (params.workdir !== undefined && params.workdir !== "") {
    const rules = pathRules(policy, agent, agent.workspace);
    const why = checkPath(rules, params.workdir, "read");
    if (why) return { ok: false, reason: `workdir: ${why}` };
    cwd = resolve(agent.workspace, expandHome(String(params.workdir), policy.home));
  }

  const parsed = parseCommand(command);
  if (!parsed.ok) return { ok: false, reason: `command refused: ${parsed.reason}` };
  const rules = pathRules(policy, agent, cwd);
  const rewrites: { start: number; end: number; to: string }[] = [];
  for (const seg of parsed.segments) {
    const verdict = checkSegment(policy, agent, seg, cwd);
    if ("reason" in verdict) return { ok: false, reason: verdict.reason };
    rewrites.push(...verdict.rewrites);
    for (const path of seg.catReads) {
      if (path.startsWith("~")) return { ok: false, reason: `$(cat ${path}): ~ is expanded by the shell, not by this guard` };
      const why = checkPath(rules, path, "read");
      if (why) return { ok: false, reason: `$(cat ${path}): ${why}` };
    }
    for (const r of seg.redirects) {
      if (r.target.text === "/dev/null") continue;
      if (r.target.text.startsWith("~")) return { ok: false, reason: `redirect ${r.op} ${r.target.text}: ~ is expanded by the shell, not by this guard` };
      const why = checkPath(rules, r.target.text, r.op === "<" ? "read" : "write");
      if (why) return { ok: false, reason: `redirect ${r.op} ${r.target.text}: ${why}` };
    }
  }
  let out = command;
  for (const r of rewrites.sort((a, b) => b.start - a.start)) out = out.slice(0, r.start) + r.to + out.slice(r.end);
  return { ok: true, command: out, cwd };
}
