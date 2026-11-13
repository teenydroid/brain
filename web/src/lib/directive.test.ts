import { describe, expect, it } from "vitest"
import { parseDirective } from "./directive"

const SAMPLE = `# DIRECTIVE

## The test

- [x] not a level item

## Level 0: wired up

The body exists and boots by itself.

- [x] Spine builds.
- [x] Doctor gates every boot.
- [ ] Notices when its own brain is dead.
  *Proof:* an alarm issue.

## Level 5: self-evolution

### a sub-heading is not the tagline

It changes itself and keeps only what wins (\`docs/evolve.md\`).

- [ ] bin/evolve, human-gated first.
- [ ] Ten generations promoted.

## How to use this file

- [ ] not a level item either
`

describe("the race, read from DIRECTIVE.md", () => {
  it("counts proven and open items per level, and nothing outside the levels", () => {
    expect(parseDirective(SAMPLE)).toEqual([
      { n: 0, title: "wired up", tagline: "The body exists and boots by itself.", done: 2, total: 3 },
      { n: 5, title: "self-evolution", tagline: "It changes itself and keeps only what wins.", done: 0, total: 2 },
    ])
  })

  it("finds no race in a file with no levels", () => {
    expect(parseDirective("# nothing here\n- [x] stray")).toEqual([])
  })
})
