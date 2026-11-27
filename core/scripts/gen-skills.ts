// Writes one OpenClaw skill per hand: brain/skills/hands/<name>/SKILL.md.
//
//   node --experimental-strip-types core/scripts/gen-skills.ts [--out DIR]
//
// The hands are binaries, and the OpenClaw agent calls them through its shell
// tool. The skill is what tells it which hand does what. Every fact in it is
// read from files, never from running a hand: the shim's docstring (what the
// hand is), the hand's own Python source (its verbs and flags), and
// docs/hands.md (each verb's R/A/O/J marker and what it does). The output is
// deterministic: sorted, no timestamps, so a stale file is a plain diff.
//
// Self-contained on purpose (node builtins only): it runs without a build.

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** In hands/ but not a hand. */
const NOT_HANDS = new Set(["README.md", "lib", "tests"]);
/** OpenClaw's description guidance: one line, under 160 characters. */
const MAX_DESCRIPTION = 160;
/** A verb's "Does" cell is cut to its first sentence, at most this long. */
const MAX_DOES = 280;

export type Marker = "R" | "A" | "O" | "J";

export interface DocVerb {
  usage: string;
  marker: Marker;
  does: string;
}

export interface HandInfo {
  name: string;
  description: string;
  module: string;
  sourceVerbs: string[];
  sourceFlags: string[];
  docVerbs: DocVerb[];
}

export function repoRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
}

/** Every executable in hands/ that is a hand, sorted. */
export function listHands(root: string): string[] {
  const dir = join(root, "hands");
  return readdirSync(dir)
    .filter((n) => !NOT_HANDS.has(n))
    .filter((n) => {
      const st = statSync(join(dir, n));
      return st.isFile() && (st.mode & 0o111) !== 0;
    })
    .sort();
}

function sourceFileFor(root: string, module: string): string | undefined {
  const base = join(root, "hands", "lib", ...module.split("."));
  for (const candidate of [`${base}.py`, join(base, "__init__.py")]) {
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

function unique(items: Iterable<string>): string[] {
  return [...new Set(items)].sort();
}

/** The Python the hand's shim runs: description, module, verbs and flags. */
function readSource(root: string, name: string): Pick<HandInfo, "description" | "module" | "sourceVerbs" | "sourceFlags"> {
  const shim = readFileSync(join(root, "hands", name), "utf8");
  const doc = /"""hands\/[\w-]+:\s*(.*?)"""/s.exec(shim)?.[1] ?? "";
  const description = doc
    .replace(/\s*\(docs\/[^)]*\)\.?\s*$/, "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\.$/, "");
  const module = /from (teeny_hands[\w.]*) import main/.exec(shim)?.[1] ?? "";
  const file = module ? sourceFileFor(root, module) : undefined;
  if (!file) return { description, module, sourceVerbs: [], sourceFlags: [] };
  const text = readFileSync(file, "utf8");
  const verbs = [...text.matchAll(/\b(?:add_parser|verb)\(\s*["']([\w-]+)["']/g)].map((m) => m[1] as string);
  const flags = [...text.matchAll(/add_argument\(\s*["'](--[\w-]+)["']/g)].map((m) => m[1] as string);
  return { description, module, sourceVerbs: unique(verbs), sourceFlags: unique(flags) };
}

/** Markdown table cells, split on `|` not escaped as `\|`. */
function cells(line: string): string[] {
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split(/(?<!\\)\|/)
    .map((c) => c.trim());
}

function firstSentence(text: string): string {
  const flat = text.replace(/\\\|/g, "|").replace(/\s+/g, " ").trim();
  const end = /[.!?](?=\s+[A-Z`(])/.exec(flat);
  let out = end ? flat.slice(0, end.index + 1) : flat;
  if (out.length > MAX_DOES) out = `${out.slice(0, MAX_DOES - 1).trimEnd()}…`;
  return out;
}

/** The rows of docs/hands.md's section for a hand that carry an R/A/O/J marker. */
export function docVerbs(handsDoc: string, name: string): DocVerb[] {
  const lines = handsDoc.split("\n");
  const out: DocVerb[] = [];
  let inSection = false;
  for (const line of lines) {
    if (line.startsWith("## ")) {
      const head = line.slice(3).split(":")[0] ?? "";
      const named = [...head.matchAll(/`([\w-]+)`/g)].map((m) => m[1]);
      inSection = named.includes(name);
      continue;
    }
    if (!inSection || !line.startsWith("|")) continue;
    const c = cells(line);
    const marker = c[1];
    if (c.length < 3 || !marker || !/^[RAOJ]$/.test(marker)) continue;
    const usage = (c[0] ?? "").replace(/\\\|/g, "|");
    if (!usage) continue;
    out.push({ usage, marker: marker as Marker, does: firstSentence(c.slice(2).join(" | ")) });
  }
  return out;
}

export function readHand(root: string, name: string, handsDoc: string): HandInfo {
  return { name, ...readSource(root, name), docVerbs: docVerbs(handsDoc, name) };
}

function cellText(text: string): string {
  return text.replace(/\|/g, "\\|");
}

function oneLine(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > MAX_DESCRIPTION ? `${flat.slice(0, MAX_DESCRIPTION - 1).trimEnd()}…` : flat;
}

const MARKERS: Record<Marker, string> = {
  R: "read: no side effect beyond its read cache",
  A: "acting: posts, sends, pays or opens something in the world",
  O: "operator-only: the operator runs it, never you",
  J: "job: writes state or calls the core, says nothing to anyone; scheduled jobs run it, you do not",
};

/** The SKILL.md of one hand. */
export function renderSkill(hand: HandInfo): string {
  const { name } = hand;
  const description = oneLine(
    `Run hands/${name} through your shell tool: ${hand.description || `the ${name} hand`}. Read this before calling it.`,
  );
  const meta = { openclaw: { requires: { bins: [name] } } };
  const out: string[] = [];
  out.push("---");
  out.push(`name: hand-${name}`);
  out.push(`description: ${JSON.stringify(description)}`);
  out.push(`metadata: ${JSON.stringify(meta)}`);
  out.push("---");
  out.push("");
  out.push(`<!-- generated by core/scripts/gen-skills.ts from hands/${name}, its source and docs/hands.md; do not edit -->`);
  out.push("");
  out.push(`# hands/${name}`);
  out.push("");
  out.push(hand.description ? `${hand.description}.` : `The ${name} hand.`);
  out.push("");
  out.push("## How to call it");
  out.push("");
  out.push(`Run it from the repository root with your shell (exec) tool: \`hands/${name} <verb> [flags]\`.`);
  out.push("");
  out.push("- A verb that takes a body reads one JSON object from stdin. Everything else is flags.");
  out.push("- It prints one JSON object on stdout. Exit 0 is success. Exit 2 is a refusal by a rule, the guard or a cap: read the `rule` and `reason`, and do not retry it unchanged. Any other exit is an error.");
  out.push("- Secrets belong to the hand; you never see them and never pass one.");
  out.push(`- The hand runs the guard, its caps and \`DRY_RUN\` itself. \`DRY_RUN\` is on unless the box turned it off; under it an acting verb checks every rule and sends nothing. Do not try to go around the hand, and do not look for another way to do what it refused.`);
  out.push("");

  const acting = hand.docVerbs.filter((v) => v.marker === "A");
  const jobs = hand.docVerbs.filter((v) => v.marker === "J");
  const operator = hand.docVerbs.filter((v) => v.marker === "O");
  const reads = hand.docVerbs.filter((v) => v.marker === "R");
  out.push("## Side effects");
  out.push("");
  if (hand.docVerbs.length === 0) {
    out.push(`docs/hands.md does not mark this hand's verbs as read, acting, job or operator. Treat every verb as acting unless the table below or \`hands/${name} --help\` says it only reads.`);
  } else {
    out.push(`- Acting (side effects in the world, each goes through the guard and the caps): ${verbList(acting)}`);
    out.push(`- Job (writes state or calls the core): ${verbList(jobs)}`);
    out.push(`- Operator only (never call): ${verbList(operator)}`);
    out.push(`- Read only: ${verbList(reads)}`);
  }
  out.push("");

  out.push("## Verbs");
  out.push("");
  if (hand.docVerbs.length > 0) {
    out.push("| Verb | Kind | What it does |");
    out.push("|---|---|---|");
    for (const v of hand.docVerbs) {
      out.push(`| ${cellText(v.usage)} | ${v.marker} | ${cellText(v.does)} |`);
    }
    out.push("");
    out.push("Kinds: " + (Object.keys(MARKERS) as Marker[]).map((k) => `${k} = ${MARKERS[k]}`).join("; ") + ".");
  } else {
    out.push(`docs/hands.md has no verb table for this hand.`);
  }
  out.push("");
  if (hand.sourceVerbs.length > 0) {
    out.push(`Verbs named in the hand's source: ${hand.sourceVerbs.map((v) => `\`${v}\``).join(", ")}.`);
    out.push("");
  }
  if (hand.sourceFlags.length > 0) {
    out.push(`Flags named in the hand's source: ${hand.sourceFlags.map((f) => `\`${f}\``).join(", ")}.`);
    out.push("");
  }
  out.push(`For one verb's exact flags, run \`DRY_RUN=1 hands/${name} <verb> --help\`. The full account is in docs/hands.md.`);
  out.push("");
  return out.join("\n");
}

function verbList(verbs: DocVerb[]): string {
  if (verbs.length === 0) return "none";
  return verbs.map((v) => v.usage.split("`").filter((_, i) => i % 2 === 1).join(", ") || v.usage).join("; ");
}

/** Every skill, keyed by its path relative to the output directory. */
export function generate(root: string): Map<string, string> {
  const handsDoc = readFileSync(join(root, "docs", "hands.md"), "utf8");
  const files = new Map<string, string>();
  for (const name of listHands(root)) {
    files.set(`${name}/SKILL.md`, renderSkill(readHand(root, name, handsDoc)));
  }
  return files;
}

export function write(files: Map<string, string>, outDir: string): void {
  for (const [rel, text] of [...files].sort(([a], [b]) => (a < b ? -1 : 1))) {
    const path = join(outDir, rel);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
  }
}

function main(argv: string[]): number {
  const root = repoRoot();
  let outDir = join(root, "brain", "skills", "hands");
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") outDir = resolve(argv[++i] ?? "");
    else {
      process.stderr.write("usage: gen-skills.ts [--out DIR]\n");
      return 2;
    }
  }
  const files = generate(root);
  write(files, outDir);
  process.stdout.write(`${files.size} hand skills written to ${outDir}\n`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main(process.argv.slice(2));
}
