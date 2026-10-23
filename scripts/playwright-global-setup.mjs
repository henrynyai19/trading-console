import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";

const address = "http://127.0.0.1:3101";

export default async function startIsolatedPreview() {
  const nextCli = resolve("node_modules/next/dist/bin/next");
  const env = { ...process.env, PLAYWRIGHT_TEST: "true" };
  const build = spawnSync(process.execPath, [nextCli, "build"], { stdio: "inherit", env });
  if (build.status !== 0) throw new Error(`Isolated Playwright build failed with exit code ${build.status ?? "unknown"}.`);

  const server = spawn(process.execPath, [nextCli, "start", "--hostname", "127.0.0.1", "--port", "3101"], {
    stdio: "inherit", env, windowsHide: true,
  });
  let startError;
  server.on("error", (error) => { startError = error; });
  try {
    const deadline = Date.now() + 60_000;
    while (Date.now() < deadline) {
      if (startError) throw startError;
      if (server.exitCode !== null) throw new Error(`Isolated Playwright server exited with code ${server.exitCode}.`);
      try {
        const response = await fetch(address);
        if (response.ok) return async () => {
          if (server.exitCode !== null) return;
          const closed = once(server, "close");
          server.kill();
          await Promise.race([closed, new Promise((resolveTimeout) => setTimeout(resolveTimeout, 5000))]);
        };
      } catch {
        // Keep waiting while the production server starts.
      }
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 250));
    }
    throw new Error("Isolated Playwright server did not become ready within 60 seconds.");
  } catch (error) {
    if (server.exitCode === null) {
      const closed = once(server, "close");
      server.kill();
      await Promise.race([closed, new Promise((resolveTimeout) => setTimeout(resolveTimeout, 5000))]);
    }
    throw error;
  }
}
