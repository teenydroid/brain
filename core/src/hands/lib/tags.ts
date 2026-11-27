// The lab tags a write may carry (docs/hands.md, "Lab tags"): `format`, a
// short name for the shape of the post (`gm`, `take`, `confession`, ...), and
// `experiment`, the id of the lab experiment it belongs to. Both are optional
// and neither is published: the hand that writes the post keeps them in its
// ledger beside the post, so `hands/stats board` and the lab can compare
// formats by their results. A port of hands/lib/teeny_hands/tags.py.
//
// `split` takes them out of a stdin body before the hand checks the rest, so
// each hand's own list of fields stays as it was.

import { HandError } from "./hand.js";
import { pyStrip } from "./py.js";

/** Lowercase letters, digits, dashes and underscores, starting with a letter. */
const FORMAT = /^[a-z][a-z0-9_-]{0,31}$/;
export const FIELDS = ["format", "experiment"] as const;
/** The ledger columns that hold them, for `addColumns`. */
export const COLUMNS = { format: "TEXT", experiment: "INTEGER" } as const;

export interface Tags {
  format: string | null;
  experiment: number | null;
}

/**
 * The body without the tags, and the tags (each null when absent). A body
 * that is not an object is returned as it is, for the hand to refuse.
 */
export function split(body: unknown): [unknown, Tags] {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return [body, { format: null, experiment: null }];
  const source = body as Record<string, unknown>;
  const rest = Object.fromEntries(Object.entries(source).filter(([k]) => !(FIELDS as readonly string[]).includes(k)));
  let format: string | null = null;
  const rawFormat = source.format;
  if (rawFormat !== undefined && rawFormat !== null) {
    if (typeof rawFormat !== "string" || !FORMAT.test(pyStrip(rawFormat).toLowerCase())) {
      throw new HandError('"format" is a short tag: lowercase letters, digits, - and _, at most 32');
    }
    format = pyStrip(rawFormat).toLowerCase();
  }
  let experiment: unknown = source.experiment ?? null;
  if (experiment !== null) {
    if (typeof experiment === "string" && /^\d+$/.test(pyStrip(experiment))) experiment = Number(pyStrip(experiment));
    if (typeof experiment !== "number" || !Number.isInteger(experiment) || experiment < 1) {
      throw new HandError('"experiment" is a lab experiment id (a positive whole number)');
    }
  }
  return [rest, { format, experiment: experiment as number | null }];
}
