import { expect, test } from '@playwright/test'
import { login } from './helpers'

test('窄屏导航为抽屉：可打开、Esc 关闭、跳转后自动收起', async ({ page }) => {
  await login(page)

  const toggle = page.getByRole('button', { name: '打开导航菜单' })
  await expect(toggle).toBeVisible()

  const aside = page.locator('.aside')
  await toggle.click()
  await expect(aside).toHaveClass(/aside--open/)
  await expect(page.locator('.drawer-backdrop')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(aside).not.toHaveClass(/aside--open/)

  await toggle.click()
  await page.getByRole('menuitem', { name: '问答' }).click()
  await expect(page).toHaveURL(/\/qa$/)
  await expect(aside).not.toHaveClass(/aside--open/)
})
