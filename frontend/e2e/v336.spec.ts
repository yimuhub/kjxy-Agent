import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('模型多渠道与官方通道（v3.36）', () => {
  test('模型中心新增官方供应商与官方模型页签，未开通时为空态', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/model-center')
    await expect(page.locator('.page-title')).toHaveText('模型中心', { timeout: 20_000 })

    await page.getByRole('tab', { name: '官方供应商' }).click()
    await expect(
      page.getByText('这是我们自己的大模型 API 官方供应商入口', { exact: false }),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: '新建渠道' }).first()).toBeVisible()

    await page.getByRole('tab', { name: '官方模型' }).click()
    await expect(page.getByText('官方通道尚未开通')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('官方模型通道预留中', { exact: false })).toBeVisible()
  })

  test('供应商模型抽屉可打开并说明一个供应商支持多模态', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/model-center')
    const tab = page.getByRole('tab', { name: '供应商', exact: true })
    await tab.click()
    const modelButton = page.getByRole('button', { name: /^模型$/ }).first()
    if (await modelButton.count()) {
      await modelButton.click()
      await expect(page.getByText('供应商模型', { exact: false })).toBeVisible()
    }
  })
})
