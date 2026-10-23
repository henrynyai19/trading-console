export default {
  reactStrictMode: true,
  // Keep Playwright's build isolated from the preview's .next directory.
  distDir: process.env.NEXT_DIST_DIR ?? (process.env.PLAYWRIGHT_TEST === "true" ? ".next-e2e" : ".next"),
};
