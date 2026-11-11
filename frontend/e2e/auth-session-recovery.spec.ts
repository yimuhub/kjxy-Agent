import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * 会话失效后的登录可达性回归（v3.78）。
 * 用户实测「有的地方走不通」： returning 用户令牌过期后直接打开 /login，
 * 页面只剩一句「账号权限加载失败」、登录框根本不出现，无法重新登录。
 * 根因：401 拦截器清了 localStorage 却没重置 Pinia 的 token ref，
 * isLoggedIn() 仍报已登录，/login 公开路由守卫把用户从登录框弹走、导航被中止。
 * 修复后 isLoggedIn() 以本机持久化令牌为准。这里守住三条恢复路径：
 *   A. 失效令牌 + 直接进 /login：登录框必须出现，且能真正登录进去；
 *   B. 失效令牌 + 进受保护首页：必须回落到登录框，不卡空白；
 *   C. 正常登录不受影响（对照）。
 */
const STALE = 'stale.invalid.token'

async function plantStaleSession(page: import('@playwright/test').Page) {
  // 先落到同源公开页，才能写 localStorage；再种入失效令牌模拟过期会话
  await page.goto('/login')
  await page.evaluate(t => {
    localStorage.setItem('ky_token', t)
    localStorage.setItem('ky_refresh_token', 'stale.invalid.refresh')
  }, STALE)
}

test.describe('会话失效后登录可达性（v3.78）', () => {
  test('失效令牌直接进 /login：登录框出现并能重新登录', async ({ page }) => {
    await plantStaleSession(page)
    await page.goto('/login')

    // 死路特征：只剩报错 toast、没有登录框。修复后登录框必须在
    const username = page.locator('input[autocomplete="username"]')
    await expect(username).toBeVisible()
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
    await expect(page.locator('body')).not.toContainText('账号权限加载失败')

    // 不只是渲染出来，还要能真正登录进去
    await username.fill('yard_admin')
    await page.locator('input[autocomplete="current-password"]').fill('yard123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    // 恢复后 URL 带 ?redirect=/chat，登录成功会落到该页而非根路径，只断言已离开登录页
    await expect(page).not.toHaveURL(/\/login/)
    await expect(page.locator('.el-breadcrumb')).toContainText('演示科技小院')
  })

  test('失效令牌进受保护首页：回落到登录框，不卡空白', async ({ page }) => {
    await plantStaleSession(page)
    await page.goto('/')
    await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  })

  test('对照：干净状态正常登录不受影响', async ({ page }) => {
    await login(page)
    await expect(page.locator('.el-breadcrumb')).toContainText('演示科技小院')
  })
})