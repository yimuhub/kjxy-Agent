import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('插件与技能库', () => {
  test('内置农业技能离线可见，预览弹窗展示来源与定义', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    await expect(page.getByRole('heading', { name: '插件与技能库' })).toBeVisible()

    const cards = page.locator('.lib-card')
    await expect(cards.first()).toBeVisible({ timeout: 15_000 })
    expect(await cards.count()).toBeGreaterThan(0)

    await cards.first().getByRole('button', { name: /预览/ }).click()
    // 弹窗统一从屏幕中央弹出，右侧抽屉已全部换成 el-dialog（destroy-on-close，DOM 里只有开着的这一个）
    const dialog = page.locator('.el-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('许可证', { exact: true })).toBeVisible()
    await expect(dialog.getByText('定义摘要', { exact: true })).toBeVisible()
    await expect(dialog.getByText('来源链接', { exact: true })).toBeVisible()
  })
})
