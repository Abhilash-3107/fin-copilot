import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './journeys',
  globalSetup: './global-setup.js',
  globalTeardown: './global-teardown.js',
  // A committed test.only would silently skip the rest of the suite.
  forbidOnly: !!process.env.CI,
  // Every test boots its own app instance on its own port, so tests share nothing.
  fullyParallel: true,
  workers: process.env.CI ? 2 : '50%',
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [
    ['list'],
    ['./hygiene-reporter.js'],
    ...(process.env.CI ? [['html', { open: 'never' }]] : []),
  ],
  use: {
    ...devices['Desktop Chrome'],
    viewport: { width: 1440, height: 900 },
    // Pinned so formatted dates and numbers do not depend on the machine.
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
    // An action that cannot proceed must fail as an assertion, not as a test
    // timeout: a timed-out test is never an expected failure.
    actionTimeout: 10_000,
    navigationTimeout: 30_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium' }],
})
