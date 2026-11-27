// A port of Python's shlex.split(s) in its default, POSIX mode with comments
// off, which is how bin/lib/playbook.py splits an input's `run`. The same
// quoting rules, and the same two errors.

export class ShlexError extends Error {}

const WHITESPACE = " \t\r\n";
/** Characters that may be escaped inside double quotes. */
const ESCAPED_IN_DOUBLE = '\\"';

export function shlexSplit(input: string): string[] {
  const tokens: string[] = [];
  let token = "";
  let inToken = false;
  let i = 0;
  while (i < input.length) {
    const ch = input[i] as string;
    if (WHITESPACE.includes(ch)) {
      if (inToken) {
        tokens.push(token);
        token = "";
        inToken = false;
      }
      i++;
    } else if (ch === "\\") {
      if (i + 1 >= input.length) throw new ShlexError("No escaped character");
      token += input[i + 1];
      inToken = true;
      i += 2;
    } else if (ch === "'") {
      const end = input.indexOf("'", i + 1);
      if (end === -1) throw new ShlexError("No closing quotation");
      token += input.slice(i + 1, end);
      inToken = true;
      i = end + 1;
    } else if (ch === '"') {
      i++;
      let closed = false;
      while (i < input.length) {
        const c = input[i] as string;
        if (c === '"') {
          closed = true;
          i++;
          break;
        }
        if (c === "\\") {
          if (i + 1 >= input.length) throw new ShlexError("No escaped character");
          const next = input[i + 1] as string;
          // In double quotes a backslash only escapes \ and ", else it stays.
          token += ESCAPED_IN_DOUBLE.includes(next) ? next : `\\${next}`;
          i += 2;
        } else {
          token += c;
          i++;
        }
      }
      if (!closed) throw new ShlexError("No closing quotation");
      inToken = true;
    } else {
      token += ch;
      inToken = true;
      i++;
    }
  }
  if (inToken) tokens.push(token);
  return tokens;
}
