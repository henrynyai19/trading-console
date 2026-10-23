import { defineConfig } from "@playwright/test";

// The backend is fully mocked in the browser tests (page.route / page.routeWebSocket).
// These tests therefore prove UI behaviour only. They do NOT prove live OANDA connectivity.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:3101", viewport: { width: 1280, height: 800 } },
  globalSetup: "./scripts/playwright-global-setup.mjs",
});
