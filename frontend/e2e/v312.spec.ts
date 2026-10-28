import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('v3.11-3.12 新页面冒烟', () => {
  test('告警与工单页可打开并弹出新建工单', async ({ page }) => {
    await login(page)
    await page.goto('/work-orders')
    await expect(page.getByRole('heading', { name: '告警与工单' })).toBeVisible()
    await page.getByRole('button', { name: '新建工单' }).click()
    await expect(page.getByRole('dialog')).toContainText('新建工单')
    await page.getByRole('button', { name: '取消' }).click()
  })

  test('对话输入区有麦克风按钮', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    await expect(page.getByRole('button', { name: '语音输入' })).toBeVisible()
  })

  test('公开分享页对失效链接给出明确提示', async ({ page }) => {
    await page.goto('/share/not-a-real-token')
    await expect(page.getByRole('heading', { name: '链接不可用' })).toBeVisible()
    await expect(page.locator('.share-card')).toContainText('分享不存在或已失效')
  })

  test('诊断页可见且详情含会诊/分享入口', async ({ page }) => {
    await login(page)
    await page.goto('/diagnosis')
    await expect(page.getByRole('heading', { name: '作物诊断' })).toBeVisible()
    const view = page.locator('.el-table').getByRole('button', { name: '查看', exact: true }).first()
    if (await view.count()) {
      await view.click()
      await expect(page.getByRole('button', { name: '转专家会诊' })).toBeVisible()
      await expect(page.getByRole('button', { name: '生成只读分享' })).toBeVisible()
    }
  })
})
