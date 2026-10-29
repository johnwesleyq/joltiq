import { defineConfig, devices } from "@playwright/test";

const externalBaseUrl = process.env.SMOKE_BASE_URL?.trim();
const baseURL = externalBaseUrl || "http://127.0.0.1:3100";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  reporter: "line",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: externalBaseUrl ? undefined : {
    command: "npm run dev -- --hostname 127.0.0.1 -p 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
