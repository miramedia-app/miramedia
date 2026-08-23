import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// Unit tests only: pure helpers in `src/lib`, run in Node. No DOM environment,
// no network, no generated artifacts — so `pnpm test` works on a clean install
// and stays independent of the Next/Fumadocs generation step.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
