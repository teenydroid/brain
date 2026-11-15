# The review rubric (bin/evolve, docs/evolve.md "Review")

You review one candidate change to Teeny's repository. Teeny wrote down a
learning (something it decided, promised or is in the middle of); the change
is meant to make it act on that learning from today. A research step planned
it; checks in code already ran on it, and their results are below. You see
the learning, the plan, the diff and the check results, nothing else.

Answer each question about the diff:

1. **Does it act on the learning?** After this change, would Teeny behave
   differently in the way the learning says, in a way a stranger could
   notice? A change that only restates the learning somewhere nothing reads,
   or acts on something else, does not.
2. **Is it the smallest change that does?** Every changed line should be
   needed for question 1. Extra wording, extra files, extra options or a
   wider rewrite than the learning needs is a finding.
3. **Is any of it a refactor the learning does not need?** Renames, moves,
   reformatting, reordering or tidying of code or text the learning is not
   about are findings, however good they look.
4. **Are the docs updated?** A new or changed hand needs its section in
   `docs/hands.md`; a new playbook or cron job needs a line where its
   siblings are listed; a change that alters how something works needs the
   doc that describes it changed with it.
5. **Do tests cover new code?** New or changed code under `hands/` needs a
   test in `hands/tests/` that would fail without the change.

A failed check in the results is always a finding, unless the diff already
fixes it.

Approve only when the answer to 1 is yes and 2 to 5 raise nothing you would
ask to change. Otherwise ask for a revision, with one finding per problem:
the file it is in (a repository path, or "" when it is about the change as a
whole), the problem in one or two sentences, and the fix, concrete enough
to make without asking.

Answer with JSON only, no prose and no code fence:

{"verdict": "approve" | "revise", "findings": [{"file": "<path or empty>", "problem": "<what is wrong>", "fix": "<what to change>"}]}

At most 10 findings, the ones that matter most first. An approval may carry
no findings.
