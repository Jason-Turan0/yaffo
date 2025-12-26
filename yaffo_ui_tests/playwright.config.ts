import { defineConfig, devices } from '@playwright/test';
import dotenv from 'dotenv';

dotenv.config();

const baseURL = process.env.BASE_URL || 'http://127.0.0.1:5000';

export default defineConfig({
  testDir: './generated',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ['html', { outputFolder: 'reports/html' }],
    ['json', { outputFile: 'reports/results/test-results.json' }],
    ['list'],
  ],

  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  outputDir: 'reports/artifacts',

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],

  // Only start webServer if BASE_URL is not set (i.e., not using external server)
  ...(process.env.BASE_URL ? {} : {
    webServer: {
      command: 'cd .. && source venv/bin/activate && python -m flask run --host=127.0.0.1',
      url: 'http://127.0.0.1:5000',
      reuseExistingServer: !process.env.CI,
      timeout: 120000,
    },
  }),
});