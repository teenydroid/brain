// The argparse port against Python 3.11's own argparse: the same parser built
// both ways, the same argv through both, and the namespace, the exit status
// and the stderr text compared. Skipped where python3 is not 3.11 (the
// messages changed in 3.12); node:24's image has 3.11.

import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import { ArgumentParser, ArgumentTypeError, SystemExit, ValueError, bounded, int } from "../../../src/hands/lib/argparse.js";
import { pythonAvailable } from "./support.js";

function bigParser(): ArgumentParser {
  const root = new ArgumentParser({ prog: "hands/demo", description: "A demo hand." });
  const verbs = root.addSubparsers({ dest: "verb" });
  const top = verbs.addParser("top", { help: "the top stories" });
  top.addArgument("--max", { type: bounded(1, 30, "--max"), default: 10, help: "how many stories to read, at most thirty, and a good deal of other words to push this over one line" });
  top.addArgument("--verbose", { action: "store_true" });
  const item = verbs.addParser("item", { help: "one item" });
  item.addArgument("id", { metavar: "ID", help: "the item" });
  const search = verbs.addParser("search", { help: "search" });
  search.addArgument("query", { metavar: "QUERY" });
  search.addArgument("--tags", { default: null, choices: ["story", "comment"] });
  search.addArgument("--days", { type: bounded(1, 30, "--days"), default: null });
  search.addArgument("--seed", { type: int, default: 7 });
  const watch = verbs.addParser("watch", { help: "watch things" });
  watch.addArgument("action", { choices: ["add", "list", "remove"] });
  watch.addArgument("id", { nargs: "?", metavar: "ID" });
  watch.addArgument("--note", { default: null });
  watch.addArgument("--tag", { action: "append" });
  watch.addArgument("--coin", { required: true, metavar: "COIN" });
  return root;
}

const PY = `
import argparse, json, sys
def bounded(low, high, what):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"{what} is {low}..{high}")
        return number
    return parse
root = argparse.ArgumentParser(prog="hands/demo", description="A demo hand.")
verbs = root.add_subparsers(dest="verb")
top = verbs.add_parser("top", help="the top stories")
top.add_argument("--max", type=bounded(1, 30, "--max"), default=10, help="how many stories to read, at most thirty, and a good deal of other words to push this over one line")
top.add_argument("--verbose", action="store_true")
item = verbs.add_parser("item", help="one item")
item.add_argument("id", metavar="ID", help="the item")
search = verbs.add_parser("search", help="search")
search.add_argument("query", metavar="QUERY")
search.add_argument("--tags", default=None, choices=["story", "comment"])
search.add_argument("--days", type=bounded(1, 30, "--days"), default=None)
search.add_argument("--seed", type=int, default=7)
watch = verbs.add_parser("watch", help="watch things")
watch.add_argument("action", choices=("add", "list", "remove"))
watch.add_argument("id", nargs="?", metavar="ID")
watch.add_argument("--note", default=None)
watch.add_argument("--tag", action="append")
watch.add_argument("--coin", required=True, metavar="COIN")
cases = json.load(sys.stdin)
out = []
import io, contextlib
for argv in cases:
    err, so = io.StringIO(), io.StringIO()
    code = 0
    ns = None
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(so):
        try:
            ns = vars(root.parse_args(argv))
        except SystemExit as exc:
            code = exc.code
    out.append({"code": code, "ns": ns, "stderr": err.getvalue(), "stdout": so.getvalue()})
print(json.dumps(out))
`;

const CASES: string[][] = [
  [],
  ["top"],
  ["top", "--max", "5"],
  ["top", "--max=5"],
  ["top", "--ma", "7"],
  ["top", "--m", "7"],
  ["top", "--max"],
  ["top", "--max", "0"],
  ["top", "--max", "31"],
  ["top", "--max", "lots"],
  ["top", "--max", "5.0"],
  ["top", "--max", " 5 "],
  ["top", "--max", "-3"],
  ["top", "--verbose", "--max", "2"],
  ["top", "--verbose=1"],
  ["top", "extra"],
  ["top", "--nope"],
  ["top", "--nope", "x"],
  ["nothing"],
  ["item"],
  ["item", "42"],
  ["item", "42", "43"],
  ["item", "--", "-5"],
  ["item", "-5"],
  ["item", "-x"],
  ["item", "has space"],
  ["item", ""],
  ["search"],
  ["search", "rust"],
  ["search", "rust", "--tags", "story"],
  ["search", "--tags", "story", "rust"],
  ["search", "rust", "--tags", "video"],
  ["search", "rust", "--days", "3", "--seed", "x"],
  ["search", "rust", "--seed", "1_0"],
  ["search", "a", "b"],
  ["watch", "list", "--coin", "x"],
  ["watch", "add", "9", "--coin", "x", "--tag", "a", "--tag", "b"],
  ["watch", "--coin", "x", "add", "9"],
  ["watch", "add"],
  ["watch", "add", "9"],
  ["watch", "bogus", "--coin", "x"],
  ["watch", "add", "9", "extra", "--coin", "x"],
  ["watch", "add", "--coin", "x", "9", "--note", "n"],
  ["watch", "add", "--co", "x"],
  ["-h"],
  ["top", "-h"],
  ["--help"],
  ["-x"],
  ["--", "top"],
  ["item", "-h"],
  ["watch", "-h"],
  ["search", "--help"],
];

describe.runIf(pythonAvailable())("argparse port against Python 3.11", () => {
  const py = spawnSync("python3", ["-I", "-c", PY], { input: JSON.stringify(CASES), encoding: "utf8" });
  const expected = JSON.parse(py.stdout || "[]") as { code: number | null; ns: Record<string, unknown> | null; stderr: string; stdout: string }[];

  it("ran the Python side", () => {
    expect(py.status, py.stderr).toBe(0);
    expect(expected).toHaveLength(CASES.length);
  });

  CASES.forEach((argv, i) => {
    it(`reads ${JSON.stringify(argv)} as Python does`, () => {
      const want = expected[i]!;
      let stderr = "";
      let stdout = "";
      const err = process.stderr.write.bind(process.stderr);
      const out = process.stdout.write.bind(process.stdout);
      process.stderr.write = ((c: string) => ((stderr += c), true)) as typeof process.stderr.write;
      process.stdout.write = ((c: string) => ((stdout += c), true)) as typeof process.stdout.write;
      let code = 0;
      let ns: Record<string, unknown> | null = null;
      try {
        ns = bigParser().parseArgs(argv);
      } catch (exc) {
        if (!(exc instanceof SystemExit)) throw exc;
        code = exc.code;
      } finally {
        process.stderr.write = err;
        process.stdout.write = out;
      }
      expect(code).toBe(want.code ?? 0);
      expect(stderr).toBe(want.stderr);
      expect(stdout).toBe(want.stdout);
      if (want.ns !== null) {
        expect(ns).not.toBeNull();
        const mine = { ...(ns as Record<string, unknown>) };
        expect(mine).toEqual(want.ns);
      }
    });
  });
});

describe("type functions", () => {
  it("reports a non-number as the Python's `parse` closure did, and a range in its own words", () => {
    expect(() => bounded(1, 3, "--n")("x")).toThrow(ValueError);
    expect(() => bounded(1, 3, "--n")("9")).toThrow(ArgumentTypeError);
    expect(bounded(1, 3, "--n")("2")).toBe(2);
  });
});
