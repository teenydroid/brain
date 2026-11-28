import type { MetadataRoute } from "next"

// Rendered once at build time: the site is a static export.
export const dynamic = "force-static"

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://teenydroid.github.io"

/** The page, and /rewards (Tiny Drops). Both change as often as Teeny does. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: SITE_URL, changeFrequency: "daily", priority: 1 },
    { url: `${SITE_URL}/rewards`, changeFrequency: "daily", priority: 0.6 },
  ]
}
