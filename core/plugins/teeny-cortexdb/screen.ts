// What may go into memory, and what may come out of it into a prompt.
//
// The core's memory_learn only refuses an empty text: the callers (bin/tick, bin/reflect)
// write what Teeny itself concluded. Capture is different, it writes what was said in a
// turn, and the people and mail Teeny answers are not Teeny. So capture screens: secrets
// are blanked, text that reads like an instruction to a model is not kept at all, and the
// recalled block that rode into the turn is never written back.

const SECRETS: RegExp[] = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}/gi,
  /\bsk-[A-Za-z0-9_-]{16,}/g,
  /\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}/g,
  /\bgithub_pat_[A-Za-z0-9_]{20,}/g,
  /\bxox[abprs]-[A-Za-z0-9-]{10,}/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g,
  /\{\{keychain:[^}]*\}\}/g,
  /\b(?:api[_-]?key|secret|token|password|passwd|mnemonic|seed phrase)\b\s*[:=]\s*\S{6,}/gi,
  /\b[0-9a-fA-F]{64}\b/g,
];

/** Phrases that address a model rather than describe something: such a turn is not kept. */
const INSTRUCTIONS: RegExp[] = [
  /\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,40}\b(?:instructions?|rules?|prompts?)\b/i,
  /\b(?:reveal|print|show|repeat)\b[^.\n]{0,30}\b(?:system prompt|your instructions)\b/i,
  /\byou are now\b/i,
  /<\/?(?:system|assistant|tool|relevant-memories)\b/i,
  /\[memory\.recall\]/i,
];

/** Control characters other than tab and newline become spaces (kept out of a regex literal: no-control-regex). */
function plainControls(text: string): string {
  let out = "";
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    out += (c < 32 && c !== 9 && c !== 10 && c !== 13) || c === 127 ? " " : ch;
  }
  return out;
}

/** The block recall puts in a prompt: it is memory already, never new memory. */
const RECALL_BLOCK = /\[memory\.recall\][\s\S]*?(?:\n\n|$)/g;

export function redactSecrets(text: string): string {
  let out = text;
  for (const re of SECRETS) out = out.replace(re, "[redacted]");
  return out;
}

export function looksLikeInstruction(text: string): boolean {
  return INSTRUCTIONS.some((re) => re.test(text));
}

export type Screened = { ok: true; text: string } | { ok: false; reason: string };

/** For capture: secrets blanked, recalled memory stripped, an instruction-like text refused. */
export function screenForCapture(text: string): Screened {
  const clean = plainControls(redactSecrets(text.replace(RECALL_BLOCK, " ")));
  if (looksLikeInstruction(clean)) return { ok: false, reason: "reads like an instruction to a model" };
  const t = clean.replace(/[ \t]+/g, " ").trim();
  return t ? { ok: true, text: t } : { ok: false, reason: "nothing left" };
}

/** For an explicit memory_store: the agent chose to write it, so only secrets are blanked. */
export function screenForStore(text: string): Screened {
  const t = plainControls(redactSecrets(text)).trim();
  return t ? { ok: true, text: t } : { ok: false, reason: "nothing to store" };
}

/** A recalled text as it may sit in a system prompt: one line, no markup that could pose as a prompt section. */
export function inert(text: string): string {
  return text.replace(/\s+/g, " ").replace(/</g, "‹").replace(/>/g, "›").trim();
}
