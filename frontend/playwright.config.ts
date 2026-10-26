import { defineConfig, devices } from '@playwright/test'

/**
 * E2E 前置条件：后端已在 http://127.0.0.1:8000 运行，且执行过 `make seed-dev`
 * （演示账号 yard_admin / yard123）。前端由 webServer 自动拉起。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  // 共享同一演示小院与真实模型：并行会互相抢会话/额度，串行跑保证可复现；
  // 模型回答时长/内容不确定，允许一次重试（隔离断言本身是确定性的）
  workers: 1,
  retries: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /mobile\.spec\.ts/,
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 7'] },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
