import path from "node:path"
import { defineConfig } from "vitest/config"

// Unit tests for the data-mapping code in src/lib (and a few server-rendered
// sections): no browser, no network. `npm test`.
export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  esbuild: { jsx: "automatic" },
  test: { include: ["src/**/*.test.ts", "src/**/*.test.tsx"], environment: "node" },
})
