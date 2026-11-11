import { describe, expect, it } from "vitest"
import { toArticle } from "./articles"

describe("toArticle", () => {
  const body = { slug: "a-thought", title: "A thought", dek: "short", status: "published", createdAt: "2026-11-01T00:00:00Z", markdown: "# hi" }

  it("leaves the word count unknown when the bot did not send one, rather than 0", () => {
    expect(toArticle(body).wordCount).toBeUndefined()
  })

  it("keeps a word count the bot sent", () => {
    expect(toArticle({ ...body, wordCount: 812 }).wordCount).toBe(812)
  })
})
