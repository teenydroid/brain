// Whose words a guard hold held: Teeny's model's, or text code wrote. A port
// of hands/lib/teeny_hands/authorship.py; read the Python docstring for the
// history (the babysitter's audit comment on PR #368). Frozen
// (evals/frozen.txt): a candidate cannot decide which of its holds count.
//
// `codeWrote(context, text)` names the recogniser that matched, or null when
// the text is (as far as these recognisers know) Teeny's model's own. Each
// recogniser is strict: it matches the generator's fixed opening and closing
// lines together. Add one to NOT_TEENYS when another hand starts posting
// composed text.

import { pyRstrip } from "./py.js";

type Json = Record<string, unknown>;

/** The babysitter's audit comment, as babysit.actions.report() writes it. */
export const BABYSIT_HEAD = "**babysitter** · ";
export const BABYSIT_FOOT = "_automated audit by hands/babysit: code reads it, code merges it, by rules only the operator changes._";
/** The closing lines it has had: a hold filed under an older one is still the babysitter's. */
export const BABYSIT_FEET: readonly string[] = [
  BABYSIT_FOOT,
  "_automated audit by hands/babysit. a person approves; the babysitter only presses the button._",
];

export interface Recogniser {
  name: string;
  what: string;
  writes: string;
  match: (context: Json, fields: Json) => boolean;
}

const babysitAudit = (context: Json, fields: Json): boolean => {
  const body = fields.body;
  return (
    context.surface === "github" &&
    context.post_kind === "comment" &&
    typeof body === "string" &&
    body.startsWith(BABYSIT_HEAD) &&
    BABYSIT_FEET.some((foot) => pyRstrip(body).endsWith(foot))
  );
};

/** Every generator of text posted under Teeny's name that Teeny's model did not write. */
export const NOT_TEENYS: readonly Recogniser[] = [
  {
    name: "babysit_audit",
    what: "the babysitter's audit comment",
    writes: "hands/lib/teeny_hands/babysit/actions.py report()",
    match: babysitAudit,
  },
];

/** The recogniser NAME as Teeny is told it. */
export function what(name: string): string {
  return NOT_TEENYS.find((r) => r.name === name)?.what ?? name;
}

function parseObject(raw: unknown): unknown {
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  if (raw instanceof Uint8Array) {
    try {
      return JSON.parse(new TextDecoder().decode(raw));
    } catch {
      return raw;
    }
  }
  return raw;
}

const isMapping = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

/** A hold's `context` column as an object ({} when it is not one). */
export function contextOf(raw: unknown): Json {
  const got = parseObject(raw);
  return isMapping(got) ? { ...got } : {};
}

/**
 * A hold's `text` column as its fields: the guard files a multi-field write as
 * a JSON object ({"title": ..., "body": ...}) and a single text as a plain
 * string, which reads here as {"body": text}.
 */
export function fieldsOf(raw: unknown): Json {
  const got = parseObject(raw);
  if (isMapping(got)) return { ...got };
  return { body: typeof got === "string" ? got : raw ? String(raw) : "" };
}

/** The NOT_TEENYS recogniser that matches a hold's CONTEXT and TEXT (raw columns or parsed), or null: Teeny's model wrote it. */
export function codeWrote(context: unknown, text: unknown): string | null {
  const ctx = contextOf(context);
  const fields = fieldsOf(text);
  return NOT_TEENYS.find((r) => r.match(ctx, fields))?.name ?? null;
}

/** Whether a hold is on Teeny's own words: not inbound, and no recogniser says code wrote it. */
export function teenysOwn(direction: unknown, context: unknown, text: unknown): boolean {
  return direction !== "inbound" && codeWrote(context, text) === null;
}
