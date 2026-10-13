import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'

const e2eDataDir = process.env.ACCSTRACE_E2E_DATA_DIR
  ?? path.resolve('..', 'data', `e2e-${process.pid}-${Date.now()}`)
process.env.ACCSTRACE_E2E_DATA_DIR = e2eDataDir
const e2eDatabaseUrl = `sqlite:///${path.join(e2eDataDir, 'accstrace.db').replaceAll('\\', '/')}`
process.env.ACCSTRACE_DATA_DIR = e2eDataDir
process.env.ACCSTRACE_DATABASE_URL = e2eDatabaseUrl

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  timeout: 120_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5181',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: '.\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8011',
      cwd: '..',
      env: {
        ...process.env,
        ACCSTRACE_DATA_DIR: e2eDataDir,
        ACCSTRACE_DATABASE_URL: e2eDatabaseUrl,
      },
      url: 'http://127.0.0.1:8011/api/health',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5181',
      env: { ...process.env, ACCSTRACE_API_URL: 'http://127.0.0.1:8011' },
      url: 'http://127.0.0.1:5181/projects',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
