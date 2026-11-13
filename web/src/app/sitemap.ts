import type { MetadataRoute } from "next"

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://teeny.enamakel.com"

/** One page, so one entry. It changes as often as Teeny does. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: SITE_URL, changeFrequency: "daily", priority: 1 }]
}
