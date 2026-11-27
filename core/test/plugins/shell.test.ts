import { describe, expect, it } from "vitest";
import { parseCommand } from "../../plugins/teeny-guardrails/shell.js";

const refused = (cmd: string): string => {
  const r = parseCommand(cmd);
  if (r.ok) throw new Error(`should be refused: ${cmd}`);
  return r.reason;
};

describe("the exec command reader", () => {
  it("reads plain words, quotes, pipes, && || ; and fd duplication", () => {
    const r = parseCommand(`hands/x post --text 'a b' "c d" | jq . && ls; echo 2>&1`);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.segments.map((s) => s.words.map((w) => w.text))).toEqual([
      ["hands/x", "post", "--text", "a b", "c d"],
      ["jq", "."],
      ["ls"],
      ["echo"],
    ]);
    expect(r.segments.map((s) => s.joiner)).toEqual(["|", "&&", ";", ""]);
  });

  it("refuses everything that could run something unread", () => {
    expect(refused("echo `id`")).toMatch(/backticks/);
    expect(refused('echo "`id`"')).toMatch(/backticks/);
    expect(refused("echo $(id)")).toMatch(/\$/);
    expect(refused('echo "$(id)"')).toMatch(/substitution/);
    expect(refused("echo $HOME")).toMatch(/\$/);
    expect(refused('echo "$HOME"')).toMatch(/\$/);
    expect(refused("echo ${HOME}")).toMatch(/\$/);
    expect(refused("sleep 1 &")).toMatch(/background/);
    expect(refused("a & b")).toMatch(/background/);
    expect(refused("cat <<EOF\nx\nEOF")).toMatch(/here-document|newline/);
    expect(refused("cat <<< hi")).toMatch(/here-document/);
    expect(refused("diff <(ls) <(ls)")).toMatch(/process substitution/);
    expect(refused("(ls)")).toMatch(/subshell/);
    expect(refused("{ ls; }")).toMatch(/grouping/);
    expect(refused("ls\nid")).toMatch(/newline/);
    expect(refused("ls\\\nid")).toMatch(/newline/);
    expect(refused("echo hi &> out")).toMatch(/background/);
    expect(refused("ls |& cat")).toMatch(/\|&/);
    expect(refused("ls >| f")).toMatch(/not read/);
    expect(refused("echo 'unclosed")).toMatch(/unclosed/);
    expect(refused('echo "unclosed')).toMatch(/unclosed/);
    expect(refused("")).toMatch(/empty/);
    expect(refused("ls &&")).toMatch(/ends after/);
    expect(refused("ls; ; ls")).toMatch(/empty part/);
    expect(refused("echo ok; ;")).toMatch(/empty part/);
    expect(parseCommand("echo ok;").ok).toBe(true);
    // the cat exception takes a literal file operand after a blank or tab, never a no-break space, "-" or an option
    expect(parseCommand('bin/teeny pr "$(cat note.txt)"').ok).toBe(true);
    expect(parseCommand('bin/teeny pr "$(cat\tnote.txt)"').ok).toBe(true);
    for (const c of ['x "$(cat\u00a0note.txt)"', 'x "$(cat -)"', 'x "$(cat -n)"']) expect(refused(c), c).toMatch(/command substitution/);
  });

  it("keeps a quoted metacharacter as text", () => {
    const r = parseCommand(`echo ';' '&&' '$(id)' "a;b" '\`x\`'`);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.segments).toHaveLength(1);
    expect(r.segments[0]?.words.map((w) => w.text)).toEqual(["echo", ";", "&&", "$(id)", "a;b", "`x`"]);
  });

  it("reads redirects as targets to be judged, and only the plain ones", () => {
    const r = parseCommand("ls > out.txt 2>/dev/null >> log < in.txt");
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.segments[0]?.redirects.map((x) => [x.op, x.target.text])).toEqual([[">", "out.txt"], [">", "/dev/null"], [">>", "log"], ["<", "in.txt"]]);
    expect(refused("ls > $(echo x)")).toMatch(/plain word/);
    expect(refused("ls > *.txt")).toMatch(/glob/);
    expect(refused("ls >")).toMatch(/no target/);
    expect(refused("ls >&file")).toMatch(/not read/);
  });

  it('reads one substitution, "$(cat PATH)", and records the path to be checked', () => {
    const r = parseCommand(`bin/teeny pr "$(cat work/pr.json)"`);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.segments[0]?.catReads).toEqual(["work/pr.json"]);
    expect(refused(`bin/teeny pr "$(cat work/pr.json; id)"`)).toMatch(/substitution/);
    expect(refused(`bin/teeny pr "$(cat $(id))"`)).toMatch(/substitution/);
    expect(refused(`bin/teeny pr $(cat work/pr.json)`)).toMatch(/\$/);
    expect(refused(`echo "$(cat a b)"`)).toMatch(/substitution/);
  });

  it("marks globs only when unquoted", () => {
    const r = parseCommand(`ls *.md "*.txt"`);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.segments[0]?.words.map((w) => w.glob)).toEqual([false, true, false]);
  });
});
