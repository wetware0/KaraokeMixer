import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

export default defineConfig({
  // Vitest needs the "browser" export condition to pick up Svelte's dev-mode
  // build; forcing it unconditionally also disabled Vite's "production"
  // condition for `vite build`, defeating tree-shaking of dev-only code and
  // bloating the production bundle. Only apply it under vitest.
  resolve: process.env.VITEST ? { conditions: ["browser"] } : undefined,
  plugins: [svelte()],
  server: {
    proxy: {
      "/api": { target: "http://localhost:8000", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
