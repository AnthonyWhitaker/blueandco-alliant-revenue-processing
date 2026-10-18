import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.E2E_PORT ?? 8600);

/**
 * End-to-end tests against the real API and the production build, on a fresh
 * synthetic SQLite database per run (e2e/start-server.sh). Run with `npm run e2e`.
 */
export default defineConfig({
  testDir: './e2e',
  // *.e2e.ts, so Vitest (which picks up *.test.*) never runs these.
  testMatch: '**/*.e2e.ts',
  // One database shared by the specs: run them one at a time.
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${String(port)}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'npm run build && bash e2e/start-server.sh',
    url: `http://127.0.0.1:${String(port)}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { E2E_PORT: String(port) },
  },
});
