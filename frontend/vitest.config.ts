import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const resolvePath = (segment: string) => path.resolve(__dirname, segment);

export default defineConfig({
  plugins: [
    react(),
    {
      name: "virtual-edition-overlay-stub",
      resolveId(id) {
        if (id === "virtual:edition-overlay")
          return "\0virtual:edition-overlay";
      },
      load(id) {
        if (id === "\0virtual:edition-overlay")
          return "export const overlayProfile = null;";
      },
    },
  ],
  resolve: {
    alias: {
      "@valuz/shared": resolvePath("./packages/shared/src"),
      "@valuz/core": resolvePath("./packages/core/src"),
      "@valuz/ui": resolvePath("./packages/ui/src"),
      "@valuz/app": resolvePath("./packages/app/src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [resolvePath("./vitest.setup.ts")],
    include: [
      `${resolvePath("./apps")}/**/src/**/*.test.{ts,tsx}`,
      `${resolvePath("./packages")}/**/src/**/*.test.{ts,tsx}`,
    ],
    // ``**/node_modules/**`` alone does NOT stop the duplication: pnpm
    // symlinks every ``@valuz/*`` package into the other packages' (and
    // apps') ``node_modules``, and the include globs' ``**`` follows those
    // symlinks. Worse, the links nest
    // (``apps/desktop/node_modules/@valuz/app/node_modules/@valuz/core/…``),
    // so the same ``packages/<pkg>/src/**`` test files get collected
    // combinatorially — ~36× — ballooning one run to 10k+ tests / 1k+ files
    // and making it appear to hang. The ``/@valuz/`` path segment only ever
    // appears on those symlink-traversed copies (real sources live under
    // ``packages/<pkg>/src``), so excluding it collapses the run back to the
    // real ~57 files without dropping any genuine test.
    exclude: [
      "**/node_modules/**",
      "**/@valuz/**",
      "**/dist/**",
      "**/.turbo/**",
    ],
  },
});
