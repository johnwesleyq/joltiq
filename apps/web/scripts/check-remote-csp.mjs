process.env.JOLTIQ_REMOTE_ACCESS_FRAME_ORIGIN = "https://support.example.test:8444";

import { readFile } from "node:fs/promises";

const { default: config } = await import("../next.config.mjs?remote-csp-test");
const rules = await config.headers();
const csp = rules
  .flatMap((rule) => rule.headers)
  .find((header) => header.key === "Content-Security-Policy")?.value;

if (!csp?.includes("frame-src 'self' https://support.example.test:8444")) {
  throw new Error(`Remote-support origin is missing from frame-src: ${csp}`);
}
if (csp.includes("'unsafe-eval'")) {
  throw new Error(`Production-shaped CSP must not allow unsafe-eval: ${csp}`);
}

const playwrightConfig = await readFile(new URL("../playwright.config.ts", import.meta.url), "utf8");
if (!playwrightConfig.includes("process.env.SMOKE_BASE_URL")) {
  throw new Error("Playwright must use SMOKE_BASE_URL when an external acceptance server is supplied");
}
if (!playwrightConfig.includes("webServer: externalBaseUrl ? undefined")) {
  throw new Error("External SMOKE_BASE_URL must disable the fallback development server");
}
