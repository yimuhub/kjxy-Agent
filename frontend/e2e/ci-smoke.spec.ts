import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * CI 冒烟（审查 O4）：确定性、低依赖，只验证「能登录、能导航、能兜底」。
 * 全量业务断言在发布前任务里跑；这里失败必须阻断合并。
 * 依赖后端已 seed（yard_admin / yard123、小院名「演示科技小院」）。
 */
test.describe('CI 冒烟', () => {
  test('错误密码给出中文提示且停留在登录页', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('yard_admin')
    await page.locator('input[autocomplete="current-password"]').fill('wrong-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.locator('.el-alert')).toContainText('用户名或密码错误')
    await expect(page).toHaveURL(/\/login/)
  })

  test('登录进入概览并渲染侧栏导航', async ({ page }) => {
    await login(page)
    await expect(page).toHaveTitle(/科技小院/)
    await expect(page.locator('#main-nav')).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '能力中心' })).toBeVisible()
  })

  test('专业助手页可打开且没有网络错误提示', async ({ page }) => {
    await login(page)
    await page.goto('/agents/custom')
    await expect(page.getByRole('heading', { name: '我的智能体' })).toBeVisible()
    await expect(page.locator('.el-message--error')).toHaveCount(0)
  })

  test('未知路径显示站内 404 并可返回概览', async ({ page }) => {
    await login(page)
    await page.goto('/this-route-does-not-exist')
    await expect(page.getByText('页面不存在', { exact: true }).first()).toBeVisible()
    await page.getByRole('button', { name: '返回概览' }).click()
    await expect(page).toHaveURL(/\/$/)
  })
})
