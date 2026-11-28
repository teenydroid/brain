import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // A static export: GitHub Pages serves out/ as plain files (built and
  // published by teenydroid/teenydroid.github.io). There is no server, so
  // nothing here may read at request time; the pages render once at build time and refresh
  // in the browser from the JSON snapshots (src/lib/snapshot.ts).
  output: "export",
  // Pages serves rewards/index.html for /rewards/.
  trailingSlash: true,
  images: { unoptimized: true },
  // The bot's package.json sits one level up; keep Next from treating the
  // repo as a workspace.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;
