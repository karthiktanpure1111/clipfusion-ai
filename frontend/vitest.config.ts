import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // Windows: from a shell whose cwd has a lowercase drive letter (d:\clips\frontend, as VS
    // Code often opens it) the default pool loads the vitest runner twice (d:/ and D:/) and
    // every suite fails with "Cannot read properties of undefined (reading 'config')".
    // vmThreads resolves a single copy, so the tests pass with either casing.
    pool: "vmThreads",
  },
});
