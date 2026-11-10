import { expect, test } from '@playwright/test'

/**
 * 初始化向导（v3.74）：空库首启 → /setup 七步 → 自动登录 → 状态落库。
 * 全量套件跑的是已初始化的演示库，本用例自动跳过；空库专用栈会真正执行。
 */

const USERNAME = 'e2e_setup_admin'
const PASSWORD = 'SetupE2E2026x!'

test('空库首启：向导创建管理员与小院并自动登录', async ({ page, request }) => {
  const status = await request.get('/api/v1/setup/status')
  const initialized = (await status.json()).data.initialized as boolean
  test.skip(initialized, '演示库已初始化，向导用例只在空库运行')

  await page.goto('/')
  await expect(page).toHaveURL(/\/setup/)
  await expect(page.locator('[data-setup-step="1"]')).toBeVisible()
  await page.locator('#setup-next').click()

  // 第 2 步：弱账号与弱口令被前端拦下，修正后才能继续
  await expect(page.locator('[data-setup-step="2"]')).toBeVisible()
  await page.locator('#setup-username').fill('admin')
  await page.locator('#setup-display-name').fill('E2E 管理员')
  await page.locator('#setup-next').click()
  await expect(
    page.locator('[data-setup-step="2"] .field.err .msg', { hasText: '该名称过于常见' }),
  ).toBeVisible()

  await page.locator('#setup-username').fill(USERNAME)
  await page.locator('#setup-password').fill('short')
  await page.locator('#setup-password2').fill('short')
  await page.locator('#setup-next').click()
  await expect(
    page.locator('[data-setup-step="2"] .field.err .msg', { hasText: '至少 10 位' }),
  ).toBeVisible()

  await page.locator('#setup-password').fill(PASSWORD)
  await page.locator('#setup-password2').fill(PASSWORD)
  await page.locator('#setup-next').click()

  // 第 3 步：小院信息
  await expect(page.locator('[data-setup-step="3"]')).toBeVisible()
  await page.locator('#setup-yard-name').fill('句容葡萄科技小院')
  await page.locator('#setup-province').selectOption('江苏省')
  await page.locator('#setup-city').selectOption({ index: 1 })
  await page.locator('#setup-next').click()

  // 第 4 步：默认「稍后配置」，直接继续
  await expect(page.locator('[data-setup-step="4"]')).toBeVisible()
  await page.locator('#setup-next').click()

  // 第 5 步：品牌（默认名称已填）
  await expect(page.locator('[data-setup-step="5"]')).toBeVisible()
  await page.locator('#setup-next').click()

  // 第 6 步：确认创建
  await expect(page.locator('[data-setup-step="6"]')).toBeVisible()
  await page.locator('#setup-saved-pwd').check()
  await page.locator('#setup-create').click()

  // 第 7 步：回执写明刚创建的管理员，随后进入工作台
  await expect(page.locator('[data-setup-step="7"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-setup-step="7"]')).toContainText(USERNAME)
  await page.locator('#setup-enter').click()
  await expect(page).not.toHaveURL(/\/setup/)

  // 刷新仍保持登录；状态接口确认已初始化
  await page.reload()
  await expect(page).not.toHaveURL(/\/setup/)
  const token = await page.evaluate(() => localStorage.getItem('ky_token'))
  expect(token).toBeTruthy()

  const after = await request.get('/api/v1/setup/status')
  expect((await after.json()).data.initialized).toBe(true)
})
