// Whether a stranger's text carries a slur: the one screen the studio's
// outside material faces. Read from the guard's own lexicon (its `hate`
// category) and matched as its screen matches. A port of
// hands/lib/teeny_hands/slurs.py.

import { LEXICON } from "./guard/lexicon.js";
import { normaliseForScreen, termPattern } from "./guard/screen.js";

export const CATEGORY = "hate";

let patterns: RegExp[] | null = null;

function hatePatterns(): RegExp[] {
  patterns ??= LEXICON.filter((entry) => entry.category === CATEGORY).flatMap((entry) => entry.terms.map((term) => termPattern(term, false)));
  return patterns;
}

/** True when any text carries a term of the lexicon's `hate` category, whatever else it carries. */
export function hateful(...texts: unknown[]): boolean {
  for (const text of texts) {
    if (typeof text !== "string" || !text.trim()) continue;
    const [plain, leet] = normaliseForScreen(text, true);
    if (hatePatterns().some((p) => p.test(plain) || p.test(leet))) return true;
  }
  return false;
}
