// The exec allowlist. OpenHuman gated the shell by `allowed_commands` and by
// refusing hidden-command syntax; this reads the command (shell.ts), then
// allows each program only if it is
//   - a hand: hands/<name>, by repo-relative or absolute path, an executable
//     file directly in the repo's hands/ folder, not on this agent's deny list;
//   - the broker client this agent was given (bin/teeny in its home);
//   - a plain program name on this agent's list (read-only, writers, tools).
// A path to anything else, however spelled, is refused: the program is never
// looked up on PATH unless its name is on the list.
import { readFileSync, statSync } from "node:fs";
import { basename, dirname, join, normalize, resolve } from "node:path";
import { checkPath, expandHome, realish, type PathRules } from "./paths.js";
import { parseCommand, shellQuote, type Segment, type Word } from "./shell.js";
import type { AgentPolicy, GuardrailsPolicy, NativePolicy } from "./policy.js";

/** `acting` counts the calls to a native playbook's acting hand in the command. */
export type ExecVerdict = { ok: true; command: string; cwd: string; acting: number; actingLast: boolean } | { ok: false; reason: string };

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

/** npm, pnpm and yarn subcommands that run a program named on the command line (or fetch one and run it). */
const PM_RUNNERS = new Set(["exec", "x", "dlx", "create", "init", "explore", "npx", "pnpx"]);
/** Options that hand the package manager a command or shell to run. */
const PM_DENIED_OPTS = ["--call", "-c", "--script-shell", "--shell", "--node-options", "--userconfig", "--globalconfig"];

/**
 * npm, pnpm and yarn run programs for the agent (`npm test` runs the project's own script, which is the point of
 * allowing them), but not an arbitrary one named on the command line: `npm exec --call 'cat secrets/x'` would run
 * outside the path checks, as the Gateway's user.
 */
function judgePackageManager(name: string, args: Word[]): string | undefined {
  for (const w of args) {
    // A quoted word is still an option to npm: the shell strips the quotes before it sees it.
    if (!w.text.startsWith("-") || w.text.length < 2) continue;
    const opt = w.text.split("=")[0] as string;
    if (PM_DENIED_OPTS.includes(opt)) return `${name} ${opt} runs a command named on the command line`;
  }
  // Any word before `--`, not only the first: `npm --prefix x exec cat` has an option value in front of the subcommand.
  const end = args.findIndex((w) => w.text === "--");
  const sub = (end < 0 ? args : args.slice(0, end)).find((w) => !isFlag(w) && PM_RUNNERS.has(w.text))?.text;
  if (sub) return `${name} ${sub} runs a program named on the command line, outside the guard's checks`;
  return undefined;
}

type Rewrite = { start: number; end: number; to: string };

const sameArgv = (a: string[], b: string[]): boolean => a.length === b.length && a.every((w, i) => w === b[i]);
const startsWith = (a: string[], prefix: string[]): boolean => prefix.length <= a.length && prefix.every((w, i) => w === a[i]);

/**
 * A native playbook's shell runs exactly its own commands: each input as declared (and with DRY_RUN=1 put in
 * front of it, whatever the agent typed), its acting hand's verb (counted), and its media helpers. Any other
 * hand, or another verb of these, is refused.
 */
function judgeNative(n: NativePolicy, handName: string, args: string[]): { kind: "input" | "acting" | "helper" } | { reason: string } {
  const argv = [`hands/${handName}`, ...args];
  if (n.inputs.some((i) => sameArgv(i, argv))) return { kind: "input" };
  if (n.hand && n.hand.name === handName && n.hand.verbs.includes(args[0] ?? "")) return { kind: "acting" };
  if (n.helpers.some((h) => startsWith(argv, h))) return { kind: "helper" };
  const verbs = n.hand ? `, and hands/${n.hand.name} ${n.hand.verbs.join("/")}` : "";
  return { reason: `${n.playbook} may not run ${argv.slice(0, 3).join(" ")}: it runs its own inputs${verbs} (docs/native-playbooks.md)` };
}

function checkSegment(policy: GuardrailsPolicy, agent: AgentPolicy, seg: Segment, cwd: string): { rewrites: Rewrite[]; acting?: number } | { reason: string } {
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
    let to = shellQuote(resolved.path);
    let acting = 0;
    if (exec.native) {
      const judged = judgeNative(exec.native, basename(resolved.path), words.slice(1).map((w) => w.text));
      if ("reason" in judged) return judged;
      // An input only reads: DRY_RUN=1 is put in front of it, as bin/act forced it. The acting hand keeps the
      // environment it was started with (DRY_RUN and the kill switches reach it unchanged), plus the playbook's
      // name as TEENY_WRITER for a `hand:` output, which bin/act set the same way.
      if (judged.kind === "input") to = `DRY_RUN=1 ${to}`;
      else if (judged.kind === "acting") {
        acting = 1;
        if (exec.native.writer) to = `TEENY_WRITER=${shellQuote(exec.native.writer)} ${to}`;
      }
    }
    rewrites.push({ start: prog.start, end: prog.start + prog.text.length, to });
    // A hand or the broker client takes its own words; they own their rules (the guard, caps, DRY_RUN).
    for (const w of args) {
      if (w.glob) return { reason: "a glob in a hand's arguments could name a file the hand was not meant to read" };
    }
    return { rewrites, acting };
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
  // A native playbook hands its body to the acting hand as `printf '%s' '<json>' | hands/x post`: the words are
  // printed, never opened, so they are not paths (a post may say "and/or"). Substitution and globs are refused above.
  if (exec.native && (name === "echo" || name === "printf") && seg.joiner === "|") return { rewrites };
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
    if (["npm", "pnpm", "yarn"].includes(name)) {
      const pm = judgePackageManager(name, args);
      if (pm) return { reason: pm };
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
  if (agent.exec?.native && params.background) return { ok: false, reason: "a background exec outlives its call and its exit code is never read: run in the foreground" };
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
  const rewrites: Rewrite[] = [];
  let acting = 0;
  let lastActing = 0;
  let pipeline = true;
  for (const seg of parsed.segments) {
    if (seg.joiner !== "|" && seg.joiner !== "") pipeline = false;
    const verdict = checkSegment(policy, agent, seg, cwd);
    if ("reason" in verdict) return { ok: false, reason: verdict.reason };
    rewrites.push(...verdict.rewrites);
    acting += verdict.acting ?? 0;
    lastActing = verdict.acting ?? 0;
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
  // The command exits with its last stage: only a pipeline that ends in the acting hand speaks for it.
  return { ok: true, command: out, cwd, acting, actingLast: pipeline && lastActing > 0 && acting === 1 };
}

/**
 * How many mentions or mail one exec command marks as not worth an answer: `hands/x mark` and
 * `hands/stimuli close`. With replies, what counts as the mind having worked its inbox pass.
 */
export function countMarks(command: string): number {
  const parsed = parseCommand(command);
  if (!parsed.ok) return 0;
  let n = 0;
  for (const seg of parsed.segments) {
    let words = seg.words;
    while (words[0] && words[0].text === "timeout") {
      let k = 1;
      while (k < words.length && isFlag(words[k] as Word)) k++;
      words = words.slice(k + 1);
    }
    const prog = words[0]?.text ?? "";
    const m = /(^|\/)hands\/(x|stimuli)$/.exec(prog.includes("/") ? normalize(prog) : prog);
    if (!m) continue;
    const verb = words.slice(1).find((a) => !isFlag(a))?.text;
    if ((m[2] === "x" && verb === "mark") || (m[2] === "stimuli" && verb === "close")) n++;
  }
  return n;
}

export type ReplyCounts = { x: number; mail: number };
const REPLY_DO: Record<string, keyof ReplyCounts> = { reply_x: "x", reply_mail: "mail" };

/**
 * How many replies, by channel, one exec command would send: `hands/x reply`, `hands/mail reply` and
 * `bin/teeny do` of a `reply_x` or `reply_mail`. Read from the same parse the judge used, so a
 * `timeout N hands/x reply` or a chain counts each. A JSON body passed as "$(cat FILE)" is read from FILE (best
 * effort; one that cannot be read counts as a reply of every channel, because the guard cannot tell it is not
 * one). Used by the per-run caps in guard.ts, which count before the call.
 */
export function countReplies(command: string, cwd: string, readFile: (path: string) => string = (p) => readFileSync(p, "utf8")): ReplyCounts {
  const n: ReplyCounts = { x: 0, mail: 0 };
  const parsed = parseCommand(command);
  if (!parsed.ok) return n;
  for (const [index, seg] of parsed.segments.entries()) {
    let words = seg.words;
    while (words[0] && words[0].text === "timeout") {
      let k = 1;
      while (k < words.length && isFlag(words[k] as Word)) k++;
      words = words.slice(k + 1);
    }
    const prog = words[0]?.text ?? "";
    const args = words.slice(1);
    // `hands/./x` and `hands//x` run the same hand as `hands/x`; count what the path resolves to.
    const hand = /(^|\/)hands\/(x|mail)$/.exec(prog.includes("/") ? normalize(prog) : prog);
    if (hand) {
      // A verb that comes from a "$(cat FILE)" is unknown here: it counts as a reply, so it cannot be used to send one unseen.
      const verb = args.find((a) => !isFlag(a))?.text;
      if (verb === "reply" || verb?.includes("\u0001")) n[hand[2] as "x" | "mail"]++;
    } else if (/(^|\/)teeny$/.test(prog) && args[0]?.text === "do") {
      let found = false;
      for (const a of args.slice(1)) {
        // A "$(cat FILE)" inside a longer argument is read by the broker as one text with the file spliced in, which
        // the guard cannot see whole: it counts as a reply of every channel. A bare "$(cat FILE)" is read below.
        if (a.text.includes("\u0001")) {
          if (a.text !== "\u0001") {
            n.x++;
            n.mail++;
            found = true;
          }
          continue;
        }
        // The client decodes the argument as JSON, so `reply\u005fx` is dispatched as `reply_x`. A JSON object is
        // counted by its `do` alone: a reply's text that mentions `reply_x` is not a second reply. Anything else is
        // read as text.
        let verb: string | undefined;
        try {
          const j = JSON.parse(a.text) as unknown;
          if (typeof j === "object" && j !== null) verb = typeof (j as { do?: unknown }).do === "string" ? ((j as { do: string }).do) : "";
        } catch {
          // not JSON
        }
        for (const [name, channel] of Object.entries(REPLY_DO)) {
          if (verb !== undefined ? verb === name : new RegExp(`\\b${name}\\b`).test(a.text)) {
            n[channel]++;
            found = true;
          }
        }
      }
      if (found) continue;
      for (const path of seg.catReads) {
        // The file is read now, but the shell reads it when the command runs: an earlier part of the same command
        // (cp, a redirect, a pipe) may have changed it by then. Only the first part is read as it stands; a later one
        // counts as a reply of every channel.
        if (index > 0) {
          n.x++;
          n.mail++;
          continue;
        }
        try {
          const verb = String((JSON.parse(readFile(resolve(cwd, path))) as { do?: unknown }).do);
          if (REPLY_DO[verb]) n[REPLY_DO[verb]]++;
        } catch {
          n.x++;
          n.mail++;
        }
      }
    }
  }
  return n;
}
