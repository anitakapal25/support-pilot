import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:58001', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: `${process.env.PYTHON_EXECUTABLE || 'python'} -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 58001`,
    cwd: '..', url: 'http://127.0.0.1:58001/health/ready', reuseExistingServer: false,
    env: { APP_ENV: 'development', AGENT_PROVIDER: 'demo', COOKIE_SECURE: 'false', ALLOWED_ORIGINS: '["http://127.0.0.1:58001"]' },
  },
});
