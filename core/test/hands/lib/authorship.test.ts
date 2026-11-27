// A port of hands/tests/test_authorship.py. One thing stays behind in Python:
// rendering babysit.actions.report() to pin that the markers are the ones the
// babysitter writes. The babysitter is not ported, so that Python test keeps
// running against the Python generator, and here the body is built from the
// same head and foot the way report() lays it out.

import { describe, expect, it } from "vitest";
import * as authorship from "../../../src/hands/lib/authorship.js";

const GITHUB_COMMENT = { field: "body", fields: ["body"], number: 368, post_kind: "comment", repo: "teenydroid/brain", surface: "github" };

/** A comment shaped as report() renders one: the head, findings, and the closing line. */
function auditComment(verdict = "needs_operator", foot: string = authorship.BABYSIT_FOOT): string {
  return `${authorship.BABYSIT_HEAD}${verdict} at \`004a55c535d6aa\`\n\n- operator: frozen in bin/lib/evolve.py\n\n${foot}\n`;
}
const held = (body: string): string => JSON.stringify({ body });

describe("the babysitter's audit comment", () => {
  it("is recognised as code written, whatever the verdict", () => {
    for (const verdict of ["blocked", "failing", "needs_operator", "needs_review", "waiting", "ready"]) {
      const body = auditComment(verdict);
      expect(authorship.codeWrote(JSON.stringify(GITHUB_COMMENT), held(body)), verdict).toBe("babysit_audit");
      expect(authorship.teenysOwn("outbound", JSON.stringify(GITHUB_COMMENT), held(body))).toBe(false);
    }
  });

  it("is recognised under the closing line it used to have", () => {
    const old = auditComment("ready", authorship.BABYSIT_FEET[1]);
    expect(authorship.codeWrote(GITHUB_COMMENT, held(old))).toBe("babysit_audit");
  });

  it("has the markers the Python pinned", () => {
    expect(authorship.BABYSIT_HEAD).toBe("**babysitter** · ");
    expect(authorship.BABYSIT_FOOT).toBe("_automated audit by hands/babysit: code reads it, code merges it, by rules only the operator changes._");
    expect(authorship.BABYSIT_FEET).toHaveLength(2);
    const body = auditComment();
    expect(body.startsWith(authorship.BABYSIT_HEAD)).toBe(true);
    expect(body.trimEnd().split("\n").at(-1)).toBe(authorship.BABYSIT_FOOT);
  });

  it("reads a plain string text column as the body", () => {
    expect(authorship.codeWrote(GITHUB_COMMENT, auditComment())).toBe("babysit_audit");
  });

  it("leaves a draft of Teeny's that quotes one marker as Teeny's", () => {
    for (const body of [
      "the babysitter signs off with " + authorship.BABYSIT_FOOT,
      "**babysitter** · i think this one is fine, merging",
      authorship.BABYSIT_FOOT,
    ]) {
      expect(authorship.codeWrote(GITHUB_COMMENT, held(body)), body).toBeNull();
      expect(authorship.teenysOwn("outbound", GITHUB_COMMENT, held(body))).toBe(true);
    }
  });

  it("is not the babysitter's anywhere but a github comment", () => {
    for (const context of [{ ...GITHUB_COMMENT, surface: "moltbook" }, { ...GITHUB_COMMENT, post_kind: "pr" }, {}]) {
      expect(authorship.codeWrote(context, held(auditComment())), JSON.stringify(context)).toBeNull();
    }
  });
});

describe("whose words", () => {
  it("counts evolve's own pull request text as Teeny's", () => {
    // The proposer model wrote it: a hold on it is Teeny's to change.
    const context = { field: "body", fields: ["title", "body"], head: "teeny/gen-19-c3", post_kind: "pr", repo: "teenydroid/brain", surface: "github" };
    const text = JSON.stringify({ title: "gen-19: SOUL rule widened", body: "**What my ledgers show** ..." });
    expect(authorship.teenysOwn("outbound", JSON.stringify(context), text)).toBe(true);
  });

  it("never counts an inbound hold as Teeny's own", () => {
    expect(authorship.teenysOwn("inbound", { surface: "moltbook" }, "a stranger's comment")).toBe(false);
  });

  it("reads unreadable columns as Teeny's words", () => {
    for (const [context, text] of [[null, null], ["not json", "not json"], ["[1, 2]", "[1, 2]"], ["{}", ""]] as const) {
      expect(authorship.codeWrote(context, text), `${context} ${text}`).toBeNull();
    }
  });

  it("tells what a recogniser is, by name", () => {
    expect(authorship.what("babysit_audit")).toBe("the babysitter's audit comment");
    expect(authorship.what("nothing")).toBe("nothing");
  });
});
