import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('成员管理（负责人视角）', () => {
  test('新建成员后出现在成员表，并可按用户名搜索', async ({ page }) => {
    const suffix = Date.now().toString(36)
    const username = `m_${suffix}`

    await login(page, 'admin', 'admin123')
    await page.goto('/members')
    await expect(page.getByRole('heading', { name: '成员管理' })).toBeVisible()

    await page.getByRole('button', { name: '新建成员' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByPlaceholder('3-32 位字母、数字或下划线').fill(username)
    await dialog.getByPlaceholder('可选').first().fill('E2E 新成员')
    await dialog.getByPlaceholder('留空自动生成一次性初始密码').fill('TempPass123')
    await dialog.getByRole('button', { name: '创建成员' }).click()

    await expect(page.locator('.el-table')).toContainText(username)
    await expect(page.locator('.el-table')).toContainText('普通成员')

    await page.getByPlaceholder('搜索用户名 / 姓名 / 邮箱 / 手机号').fill(username)
    await expect(page.locator('.el-table tbody tr')).toHaveCount(1)
    await expect(page.locator('.el-table')).toContainText(username)
  })
})
