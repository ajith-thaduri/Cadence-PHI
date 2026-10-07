import { defineConfig } from '@playwright/test';

// End-to-end tests against a real console (engine + built UI). Uses the locally installed Google Chrome
// (no browser download). Start-up: the console is launched on port 8766 with the `test` engine environment
// unless PHI_UI_URL points at a running one.
const external = process.env.PHI_UI_URL;

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: external ?? 'http://127.0.0.1:8766',
    channel: 'chrome',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: external
    ? undefined
    : {
        command: '.venv/bin/python -m harness_ui serve --port 8766 --env test',
        cwd: '..',
        url: 'http://127.0.0.1:8766/api/v1/healthz',
        reuseExistingServer: true,
        timeout: 120_000,
        env: { HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1' },
      },
});
