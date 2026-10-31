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

  test('内置办公技能可见，可预览排版规范', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    const card = page.locator('.lib-card', { hasText: 'Excel 台账与数据表' })
    await expect(card).toBeVisible({ timeout: 15_000 })
    await expect(card).toContainText('Excel')
    await expect(card).toContainText('低风险')

    await card.getByRole('button', { name: /预览/ }).click()
    const dialog = page.locator('.el-dialog')
    await expect(dialog).toContainText('表头')
  })

  test('正式写作技能可检索：项目申报书与投标文件', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    const keyword = page.getByLabel('搜索插件与技能')

    await keyword.fill('项目申报书')
    await keyword.press('Enter')
    const grantCard = page.locator('.lib-card', { hasText: '项目申报书' }).first()
    await expect(grantCard).toBeVisible({ timeout: 15_000 })
    await grantCard.getByRole('button', { name: /预览/ }).click()
    await expect(page.locator('.el-dialog')).toContainText('待核实')

    await page.locator('.el-dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await keyword.fill('投标文件')
    await keyword.press('Enter')
    await expect(page.locator('.lib-card', { hasText: '投标文件' }).first()).toBeVisible({
      timeout: 15_000,
    })
  })

  test('官方库页签在未配置源时给出空态而不是报错', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    await page.getByRole('tab', { name: '官方库' }).click()
    await expect(page.getByText(/还没有配置官方库源|官方库源/).first()).toBeVisible({
      timeout: 15_000,
    })
  })
})
