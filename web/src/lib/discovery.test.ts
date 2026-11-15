import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

// The discovery files in public/.well-known (docs/roadmap.md, P9), checked against
// the required fields of the A2A AgentCard (a2a-protocol.org, specification 4.4)
// and the ERC-8004 registration file (eips.ethereum.org/EIPS/eip-8004).
const read = (name: string) => JSON.parse(readFileSync(join(__dirname, "../../public/.well-known", name), "utf8"))
const str = (v: unknown) => typeof v === "string" && v.trim().length > 0

describe("the A2A agent card", () => {
  const card = read("agent-card.json")

  it("has every field the AgentCard requires, with the right types", () => {
    for (const key of ["name", "description", "version"]) expect(str(card[key]), key).toBe(true)
    expect(Array.isArray(card.supportedInterfaces)).toBe(true)
    expect(typeof card.capabilities).toBe("object")
    for (const key of ["defaultInputModes", "defaultOutputModes"]) {
      expect(card[key].length).toBeGreaterThan(0)
      expect(card[key].every(str)).toBe(true)
    }
    expect(card.skills.length).toBeGreaterThan(0)
    for (const skill of card.skills) {
      for (const key of ["id", "name", "description"]) expect(str(skill[key]), key).toBe(true)
      expect(skill.tags.length).toBeGreaterThan(0)
    }
    expect(str(card.provider.organization) && str(card.provider.url)).toBe(true)
    for (const face of card.supportedInterfaces) {
      for (const key of ["url", "protocolBinding", "protocolVersion"]) expect(str(face[key]), key).toBe(true)
    }
  })

  it("names the operator, claims no endpoint it does not serve, and no reputation", () => {
    expect(card.provider.organization).toContain("@senamakel")
    expect(card.description).toContain("@senamakel")
    expect(card.supportedInterfaces).toEqual([])
    expect(card.securitySchemes).toBeUndefined()
    expect(JSON.stringify(card)).not.toMatch(/reputation score|trusted by|verified by/i)
  })
})

describe("the ERC-8004 registration file", () => {
  const file = read("agent-registration.json")

  it("has the registration-v1 shape", () => {
    expect(file.type).toBe("https://eips.ethereum.org/EIPS/eip-8004#registration-v1")
    for (const key of ["name", "description", "image"]) expect(str(file[key]), key).toBe(true)
    expect(file.services.length).toBeGreaterThan(0)
    for (const s of file.services) expect(str(s.name) && str(s.endpoint)).toBe(true)
    expect(typeof file.active).toBe("boolean")
    expect(typeof file.x402Support).toBe("boolean")
    expect(Array.isArray(file.registrations)).toBe(true)
  })

  it("is for discovery only: no trust model, no registration claimed, the operator named", () => {
    expect(file.supportedTrust).toEqual([])
    expect(file.registrations).toEqual([])
    expect(file.description).toContain("@senamakel")
    expect(file.services.find((s: { name: string }) => s.name === "A2A").endpoint).toMatch(/\/\.well-known\/agent-card\.json$/)
  })
})
