import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.43 指挥官工作台：
 * a. 侧栏「指挥官工作台」可进入 /commander；
 * b. 概览卡展示预算、成功率、成本、记忆资产四张卡；
 * c. 记忆检索走 /commander/memories/search，空结果也有明确提示；
 * d. 成本结构与周窗口来自同一概览接口。
 *
 * 只读接口，不触发模型，不改设置。
 */

test.describe('指挥官工作台（v3.43）', () => {
  test('从侧栏进入指挥台并检索记忆', async ({ page }) => {
    await login(page)

    const navItem = page.locator('.nav-item', { hasText: '指挥官工作台' }).first()
    await expect(navItem).toBeVisible()

    const [overview] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'GET' &&
          resp.url().includes('/api/v1/commander/overview'),
        { timeout: 30_000 },
      ),
      navItem.click(),
    ])

    expect(overview.ok()).toBeTruthy()
    const body = (await overview.json()) as {
      data: {
        commander: { token_budget: number }
        stats: { subtasks: { success_rate: number } }
        cost: { total: { calls: number }; by_purpose: unknown[] }
        memory: { counts: { total: number } }
      }
    }
    expect(typeof body.data.commander.token_budget).toBe('number')
    expect(Array.isArray(body.data.cost.by_purpose)).toBe(true)

    await expect(page).toHaveURL(/\/commander$/)
    const pageShell = page.locator('.commander-page')
    await expect(pageShell).toBeVisible()

    const cards = pageShell.locator('.stat-card')
    await expect(cards).toHaveCount(4)
    await expect(cards.nth(0)).toContainText('本轮预算')
    await expect(cards.nth(1)).toContainText('任务成功率')
    await expect(cards.nth(2)).toContainText('天成本')
    await expect(cards.nth(3)).toContainText('记忆资产')

    await expect(pageShell.locator('.panel', { hasText: '成本结构' })).toBeVisible()

    const searchPanel = pageShell.locator('.panel', { hasText: '记忆检索' })
    await expect(searchPanel).toBeVisible()
    await searchPanel.locator('input').fill('给小院做宣传 PPT')

    const [matched] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'POST' &&
          resp.url().includes('/api/v1/commander/memories/search'),
        { timeout: 30_000 },
      ),
      searchPanel.getByRole('button', { name: '检索' }).click(),
    ])
    expect(matched.ok()).toBeTruthy()
    const matchedBody = (await matched.json()) as { data: { items: unknown[] } }
    expect(Array.isArray(matchedBody.data.items)).toBe(true)

    const itemCount = matchedBody.data.items.length
    if (itemCount > 0) {
      await expect(searchPanel.locator('.memory-item')).toHaveCount(itemCount)
      await expect(searchPanel.locator('.memory-item').first()).toContainText('%')
    } else {
      await expect(searchPanel).toContainText('没有匹配到记忆')
    }
  })
})
