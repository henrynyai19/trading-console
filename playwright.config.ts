import { defineConfig } from "@playwright/test";

// The backend is fully mocked in the browser tests (page.route / page.routeWebSocket).
// These tests therefore prove UI behaviour only. They do NOT prove live OANDA connectivity.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: { baseURL: "http://localhost:3100", viewport: { width: 1280, height: 800 } },
  webServer: { command: "npm run build && npx next start -p 3100", url: "http://localhost:3100", reuseExistingServer: true, timeout: 240_000 },
});
