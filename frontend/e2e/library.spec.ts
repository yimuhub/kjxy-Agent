import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('插件与技能库', () => {
  test('内置农业技能离线可见，预览抽屉展示来源与定义', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    await expect(page.getByRole('heading', { name: '插件与技能库' })).toBeVisible()

    const cards = page.locator('.lib-card')
    await expect(cards.first()).toBeVisible({ timeout: 15_000 })
    expect(await cards.count()).toBeGreaterThan(0)

    await cards.first().getByRole('button', { name: /预览/ }).click()
    const drawer = page.locator('.el-drawer')
    await expect(drawer).toBeVisible()
    await expect(drawer.getByText('许可证', { exact: true })).toBeVisible()
    await expect(drawer.getByText('定义摘要', { exact: true })).toBeVisible()
    await expect(drawer.getByText('来源链接', { exact: true })).toBeVisible()
  })
})
