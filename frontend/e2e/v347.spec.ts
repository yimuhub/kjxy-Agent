import { expect, test } from '@playwright/test'

/**
 * v3.47 桌面伙伴（浏览器可验证部分）：
 * a. /pet 为公开路由，不登录也能打开；
 * b. 皮肤面板可打开、切换并持久化（v3.50 起含农民伯伯共 8 套）；
 * c. URL 皮肤参数优先于本地存储。
 */

test.describe('桌面伙伴与皮肤（v3.47）', () => {
  test('公开桌宠页、8 套皮肤与持久化可用', async ({ page }) => {
    await page.goto('/pet')
    await expect(page).toHaveURL(/\/pet/)

    const petPage = page.locator('.pet-page')
    await expect(petPage).toBeVisible()
    await expect(petPage.locator('.pet-figure')).toBeVisible()
    await expect(petPage.locator('.pet-name')).toContainText('田伯')
    await expect(petPage.locator('.pet-hint')).toContainText('左键呼出工作台')
    await expect(petPage.locator('.pet-bubble')).toContainText(/想先做哪件事|换衣服|呼出工作台|角落|换位置/)

    await petPage.click({ button: 'right', position: { x: 110, y: 220 } })
    const panel = page.locator('.skin-panel')
    await expect(panel).toBeVisible()
    await expect(panel.locator('.skin-chip')).toHaveCount(8)
    for (const name of ['小芽', '向日', '芋泥', '糯米', '小轨', '小火', '团子', '田伯']) {
      await expect(panel.locator('.skin-chip', { hasText: name })).toBeVisible()
    }

    await panel.locator('.skin-chip', { hasText: '团子' }).click()
    await expect(petPage.locator('.pet-name')).toContainText('团子')
    await expect(petPage.locator('.pet-svg')).toBeVisible()
    const stored = await page.evaluate(() => localStorage.getItem('ky_pet_skin'))
    expect(stored).toBe('panda')

    await page.reload()
    await expect(petPage.locator('.pet-name')).toContainText('团子')

    await page.goto('/pet?skin=orbit')
    await expect(petPage.locator('.pet-name')).toContainText('小轨')
    await expect(page.locator('.skin-panel')).toBeHidden()
  })
})
