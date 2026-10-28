import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** v3.18 单点登录：登录页第三方入口的显隐、回调页失败兜底、设置页身份源入口。 */

test.describe('单点登录（v3.18）', () => {
  test('未配置身份源时不出现第三方登录块', async ({ page }) => {
    await page.goto('/login')
    // 密码登录始终可用；没有可用身份源时不应出现分隔线与第三方按钮
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
    await expect(page.locator('.sso-block')).toHaveCount(0)
  })

  test('回调页缺少短码时给出可读原因并可返回登录', async ({ page }) => {
    await page.goto('/sso/callback')
    await expect(page.getByText('单点登录未完成')).toBeVisible()
    await expect(page.getByText('缺少登录凭据')).toBeVisible()
    await page.getByRole('button', { name: '返回登录' }).click()
    await expect(page).toHaveURL(/\/login$/)
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  })

  test('管理员在系统设置里能进入单点登录页签', async ({ page }) => {
    await login(page)
    await page.goto('/settings?tab=sso')
    await expect(page.getByRole('tab', { name: '单点登录' })).toBeVisible()
    // 管理员可查看配置，但开关身份源属保护型权限
    await expect(page.getByText('仅负责人或获得敏感授权的管理员可修改身份源配置。')).toBeVisible()
    await expect(page.getByText('身份提供方（0）')).toBeVisible()
  })
})
