import { expect, type Page } from '@playwright/test'

/** 演示小院管理员登录（依赖 make seed-dev 创建的种子账号） */
export async function login(
  page: Page,
  username = 'yard_admin',
  password = 'yard123',
) {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.locator('.el-breadcrumb')).toContainText('演示科技小院')
}
