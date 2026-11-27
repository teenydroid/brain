// Read one playbook: brain/playbooks/<name>.md, a prompt with a TOML front
// matter between `+++` lines. A faithful port of bin/lib/playbook.py: the same
// rules in the same order, the same messages, the same result shape (the keys
// keep Python's spelling, `max_per_day` included, so the two can be compared
// as JSON). The tables of what a playbook may reach are copied from it; a test
// compares the whole parse of every real playbook with playbook.py's.
//
// One difference TOML forces: a float that is a whole number (`max_per_day =
// 1.0`) reads as an integer here, where Python's tomllib keeps it a float and
// refuses it.

import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseToml } from "smol-toml";
import { shlexSplit, ShlexError } from "./shlex.js";

const NAME = /^[a-z0-9][a-z0-9-]*$/;
export const SIMPLE_OUTPUTS = ["post", "reply", "like", "none"] as const;
/** Hands bin/act never hands a draft to. */
export const NOT_OUTPUTS = ["guard", "image", "video", "reel", "meme", "keychain", "build"];
/** Hands a draft reaches only through the verbs named here. */
export const VERB_OUTPUTS: Record<string, string[]> = {
  goals: ["propose"],
  gh: ["open-issue", "comment"],
  tasks: ["add"],
  mail: ["reply", "send"],
  ladder: ["propose"],
  upstream: ["propose"],
  reddit: ["browse", "inbox", "stats", "comment", "reply", "post", "upvote", "subscribe"],
};
/** What a draft may ask a post to carry (`media`). */
export const MEDIA_KINDS = ["image", "video", "meme"];
/** Hands that hold money: a draft reaches them only through these verbs. */
export const MONEY_OUTPUTS: Record<string, string[]> = {
  wallet: [],
  bounty: ["pick"],
  x402: [],
  surplus: [],
  giveaway: ["open", "judge"],
  rent: [],
};
export const MONEY_READS: Record<string, string[]> = {
  wallet: ["address", "balance", "tiny", "ledger", "costs", "receipt", "receipts", "caps"],
  bounty: ["list", "get", "announcements"],
  x402: ["log"],
  surplus: ["status", "log"],
  giveaway: ["status", "announcements"],
  rent: ["status", "list", "get", "humans"],
};
/** The verbs that move money, never a playbook's output. */
export const NEVER_OUTPUTS: Record<string, string[]> = {
  wallet: ["pay", "swap", "bridge", "x402", "buy-tiny"],
  bounty: ["pay", "pay-due"],
  x402: ["search", "contents", "crawl", "fund"],
  surplus: ["sweep"],
  giveaway: ["entries", "draw", "settle", "clawback"],
  rent: ["post", "accept", "review", "message", "poll", "cancel"],
};
/** Hands that hold secrets: an input runs only these verbs of theirs. */
export const SECRET_READS: Record<string, string[]> = { keychain: ["list"] };
export const MAX_INPUTS = 8;
/** What a nudged playbook may ask of the mind. */
export const NUDGE_OUTPUTS = ["post", "reply", "like", "none"];
/** An input that runs a data job Teeny wrote: `datajob:<name>`. */
export const DATAJOB = "datajob:";

export class PlaybookError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PlaybookError";
  }
}

export interface PlaybookInput {
  name: string;
  argv: string[];
  required: boolean;
  optional: boolean;
}

export interface Playbook {
  name: string;
  nudge: boolean;
  description: string;
  media: string[];
  output: string;
  surface: string;
  max_per_day: number;
  inputs: PlaybookInput[];
  prompt: string;
}

export interface LoadOptions {
  /** hands/ directory: when given, a hand a playbook names must exist there. */
  handsDir?: string;
  /** The repository root, where the guard's surface list is read from. */
  repoRoot?: string;
}

export function defaultRepoRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
}

/** The guard's own surface list (holds.SURFACES), read from its source. */
export function guardSurfaces(repoRoot: string = defaultRepoRoot()): string[] {
  const text = readFileSync(join(repoRoot, "hands", "lib", "teeny_hands", "guard", "holds.py"), "utf8");
  const start = text.indexOf("SURFACES = (");
  if (start === -1) throw new PlaybookError("holds.py has no SURFACES");
  const end = text.indexOf("\n)", start);
  const body = text
    .slice(start + "SURFACES = (".length, end)
    .split("\n")
    .filter((l) => !l.trim().startsWith("#"))
    .join("\n");
  return [...body.matchAll(/"([^"]*)"/g)].map((m) => m[1] as string);
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function fullMatch(re: RegExp, value: string): boolean {
  return re.test(value);
}

function datajobArgv(run: string, where: string, brain: string): string[] {
  const name = run.slice(DATAJOB.length);
  if (!fullMatch(NAME, name)) throw new PlaybookError(`${where}: a data job input is datajob:<name>, got ${pyRepr(run)}`);
  if (!isFile(join(brain, "datajobs", `${name}.py`))) throw new PlaybookError(`${where}: no data job datajobs/${name}.py`);
  return ["datajob", name];
}

function handArgv(run: string, where: string, handsDir: string | undefined): string[] {
  let argv: string[];
  try {
    argv = shlexSplit(run);
  } catch (exc) {
    if (exc instanceof ShlexError) throw new PlaybookError(`${where}: ${exc.message}`);
    throw exc;
  }
  const first = argv[0];
  if (first === undefined || !/^hands\/[a-z0-9][a-z0-9-]*$/.test(first)) {
    throw new PlaybookError(`${where}: an input runs a hand (hands/<name> ...), got ${pyRepr(run)}`);
  }
  const hand = first.split("/", 2)[1] as string;
  if (handsDir !== undefined && !isFile(join(handsDir, hand))) {
    throw new PlaybookError(`${where}: no hand ${pyRepr(first)}`);
  }
  const reads = own(MONEY_READS, hand) ?? own(SECRET_READS, hand);
  if (reads !== undefined && (argv.length < 2 || !reads.includes(argv[1] as string))) {
    throw new PlaybookError(`${where}: an input runs only a read verb of ${first} (${reads.join(", ")})`);
  }
  return argv;
}

/** A table lookup that never reaches Object.prototype. */
function own<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

/** Python's repr of a str, for the messages that quote one. */
function pyRepr(value: unknown): string {
  if (typeof value !== "string") return String(value);
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  const body = value.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t");
  return `${quote}${quote === "'" ? body.replace(/'/g, "\\'") : body}${quote}`;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !(value instanceof Date);
}

/** A skill's text without its `---` front matter (that is for an index). */
export function skillBody(text: string): string {
  if (text.startsWith("---\n")) {
    const end = text.indexOf("\n---\n", 4);
    if (end !== -1) text = text.slice(end + 5);
  }
  return text.trim();
}

/** Split on the first two `+++`, as Python's str.split("+++", 2). */
function splitFrontMatter(text: string): string[] {
  const parts: string[] = [];
  let from = 0;
  for (let n = 0; n < 2; n++) {
    const at = text.indexOf("+++", from);
    if (at === -1) break;
    parts.push(text.slice(from, at));
    from = at + 3;
  }
  parts.push(text.slice(from));
  return parts;
}

export function loadPlaybook(brain: string, name: string, options: LoadOptions = {}): Playbook {
  const { handsDir, repoRoot } = options;
  if (!fullMatch(NAME, name)) throw new PlaybookError(`bad playbook name ${pyRepr(name)}`);
  const path = join(brain, "playbooks", `${name}.md`);
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (exc) {
    const code = (exc as NodeJS.ErrnoException).code;
    throw new PlaybookError(`${path}: ${code === "ENOENT" ? "No such file or directory" : String(code)}`);
  }
  const parts = splitFrontMatter(text);
  if (parts.length !== 3 || (parts[0] as string).trim() !== "") {
    throw new PlaybookError(`${path}: must start with a +++ TOML front matter +++ block`);
  }
  let meta: Record<string, unknown>;
  try {
    meta = parseToml(parts[1] as string) as Record<string, unknown>;
  } catch (exc) {
    throw new PlaybookError(`${path}: ${(exc as Error).message}`);
  }
  let prompt = (parts[2] as string).trim();
  if (!prompt) throw new PlaybookError(`${path}: the prompt is empty`);

  const known = new Set(["description", "output", "surface", "max_per_day", "inputs", "media", "nudge", "skills"]);
  const extra = Object.keys(meta).filter((k) => !known.has(k));
  if (extra.length > 0) throw new PlaybookError(`${path}: unknown keys: ${extra.sort().join(", ")}`);
  const description = meta.description;
  if (typeof description !== "string" || !description.trim()) throw new PlaybookError(`${path}: needs a description`);

  const output = meta.output;
  if (typeof output !== "string") throw new PlaybookError(`${path}: needs an output`);
  if (output.startsWith("hand:")) {
    const argv = output.slice("hand:".length).split(/\s+/).filter((s) => s !== "");
    if (argv.length !== 2 || !argv.every((a) => fullMatch(NAME, a))) {
      throw new PlaybookError(`${path}: output hand:<name> <verb>, got ${pyRepr(output)}`);
    }
    const [hand, verb] = argv as [string, string];
    if (NOT_OUTPUTS.includes(hand)) throw new PlaybookError(`${path}: hands/${hand} cannot be an output`);
    const verbs = own(VERB_OUTPUTS, hand);
    if (verbs !== undefined && !verbs.includes(verb)) {
      throw new PlaybookError(`${path}: hands/${hand} ${verb} cannot be an output (it may take: ${verbs.join(", ")})`);
    }
    if ((own(NEVER_OUTPUTS, hand) ?? []).includes(verb)) {
      throw new PlaybookError(`${path}: hands/${hand} ${verb} moves money and is never a playbook's output`);
    }
    const money = own(MONEY_OUTPUTS, hand);
    if (money !== undefined && !money.includes(verb)) {
      throw new PlaybookError(`${path}: hands/${hand} ${verb} cannot be an output (it may take: ${money.length ? money.join(", ") : "nothing"})`);
    }
    if (handsDir !== undefined && !isFile(join(handsDir, hand))) {
      throw new PlaybookError(`${path}: no hand hands/${hand}`);
    }
  } else if (!(SIMPLE_OUTPUTS as readonly string[]).includes(output)) {
    throw new PlaybookError(`${path}: output is one of ${SIMPLE_OUTPUTS.join(", ")} or hand:<name> <verb>, got ${pyRepr(output)}`);
  }

  const surface = "surface" in meta ? meta.surface : "x";
  const surfaces = guardSurfaces(repoRoot);
  if (typeof surface !== "string" || !surfaces.includes(surface)) {
    throw new PlaybookError(`${path}: surface is one of ${surfaces.join(", ")}, got ${pyRepr(surface)}`);
  }

  const maxPerDay = "max_per_day" in meta ? meta.max_per_day : 1;
  if (typeof maxPerDay !== "number" || !Number.isInteger(maxPerDay) || maxPerDay < 1 || maxPerDay > 100) {
    throw new PlaybookError(`${path}: max_per_day is a whole number from 1 to 100`);
  }

  const inputs = "inputs" in meta ? meta.inputs : [];
  if (!Array.isArray(inputs) || inputs.length > MAX_INPUTS) {
    throw new PlaybookError(`${path}: inputs is a list of at most ${MAX_INPUTS} [[inputs]] tables`);
  }
  const parsed: PlaybookInput[] = [];
  inputs.forEach((entry: unknown, i: number) => {
    const where = `${path}: inputs[${i}]`;
    const allowed = new Set(["name", "run", "required", "optional"]);
    if (!isPlainObject(entry) || Object.keys(entry).some((k) => !allowed.has(k)) || typeof entry.run !== "string") {
      throw new PlaybookError(`${where}: an input is {name, run, required?, optional?}`);
    }
    const required = "required" in entry ? entry.required : false;
    const optional = "optional" in entry ? entry.optional : false;
    if (typeof required !== "boolean") throw new PlaybookError(`${where}: required is true or false`);
    if (typeof optional !== "boolean") throw new PlaybookError(`${where}: optional is true or false`);
    if (optional && required) throw new PlaybookError(`${where}: an input is optional or required, not both`);
    const inputName = "name" in entry ? entry.name : `input-${i}`;
    if (typeof inputName !== "string" || !fullMatch(NAME, inputName)) {
      throw new PlaybookError(`${where}: name must be lowercase letters, digits and dashes`);
    }
    parsed.push({
      name: inputName,
      argv: entry.run.startsWith(DATAJOB) ? datajobArgv(entry.run, where, brain) : handArgv(entry.run, where, handsDir),
      required,
      optional,
    });
  });

  const media = "media" in meta ? meta.media : [];
  if (
    !Array.isArray(media) ||
    !media.every((m) => typeof m === "string" && MEDIA_KINDS.includes(m)) ||
    new Set(media).size !== media.length
  ) {
    throw new PlaybookError(`${path}: media is a list of ${MEDIA_KINDS.join(", ")}`);
  }
  if (media.length > 0 && !(output === "post" || output.startsWith("hand:"))) {
    throw new PlaybookError(`${path}: media goes with a post or a hand: output only`);
  }

  const nudge = "nudge" in meta ? meta.nudge : false;
  if (typeof nudge !== "boolean") throw new PlaybookError(`${path}: nudge is true or false`);
  if (nudge && !NUDGE_OUTPUTS.includes(output)) {
    throw new PlaybookError(`${path}: a nudge goes to the mind, which can only ${NUDGE_OUTPUTS.join(", ")}; not ${pyRepr(output)}`);
  }

  const skills = "skills" in meta ? meta.skills : [];
  if (
    !Array.isArray(skills) ||
    !skills.every((k) => typeof k === "string" && fullMatch(NAME, k)) ||
    new Set(skills).size !== skills.length
  ) {
    throw new PlaybookError(`${path}: skills is a list of distinct skill names (brain/skills/<name>/)`);
  }
  for (const skill of skills as string[]) {
    const file = join(brain, "skills", skill, "SKILL.md");
    if (!existsSync(file) || !isFile(file)) throw new PlaybookError(`${path}: no skill brain/skills/${skill}/SKILL.md`);
    prompt += `\n\n${skillBody(readFileSync(file, "utf8"))}`;
  }

  return {
    name,
    nudge,
    description: description.trim(),
    media: media as string[],
    output,
    surface,
    max_per_day: maxPerDay,
    inputs: parsed,
    prompt,
  };
}
