import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// The `@/` alias mirrors tsconfig paths. Type-only imports resolve without it
// (they're erased at transform), so a test importing a runtime value through
// `@/` is the case that needs this.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
});
