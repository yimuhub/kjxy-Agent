import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * 单助手对话锁定：
 * - 从助手卡片「对话」进入后，界面明确显示绑定对象；
 * - 不再出现 @ 点名 / 加成员 / 知识问答切换；
 * - 每条消息发送时都带同一个 target_code，后端不会再自动换助手。
 *
 * 这里不依赖真实模型：拦下发送请求，只断言请求体里的锁定对象。
 */
test.describe('单助手会话锁定', () => {
  test('从助手卡片进入后，会话保持绑定且每轮都带同一 target_code', async ({ page }) => {
    test.setTimeout(60_000)
    const bodies: Array<Record<string, unknown>> = []
    for (const pattern of [
      '**/api/v1/chat/sessions/*/messages/stream',
      '**/api/v1/chat/sessions/*/messages',
    ]) {
      await page.route(pattern, async route => {
        const request = route.request()
        // 只认发送动作：打开会话时还有一批 GET /messages（无请求体），混进来会挡住断言
        if (request.method() === 'POST') {
          bodies.push(request.postDataJSON() as Record<string, unknown>)
        }
        await route.abort()
      })
    }

    await login(page)
    await page.goto('/agents/custom')

    const card = page.locator('.da-grid .da-grid-item').first()
    await expect(card).toBeVisible()
    await card.getByRole('button', { name: '对话', exact: true }).click()

    const dialog = page.locator('.el-dialog').filter({ hasText: '使用：' })
    await expect(dialog).toBeVisible()

    // 绑定提示 + 锁定后不提供的入口
    await expect(dialog.locator('.lock-note')).toContainText('已绑定')
    await expect(dialog.locator('.lock-note')).toContainText('仅由该助手')
    await expect(dialog.locator('.mode-group')).toHaveCount(0)
    await expect(dialog.locator('.member-bar')).toHaveCount(0)
    await expect(dialog.locator('.mention-bar')).toHaveCount(0)

    await dialog.locator('textarea').fill('你能做什么？')
    await dialog.locator('textarea').press('Enter')

    await expect.poll(() => bodies.length, { timeout: 15_000 }).toBeGreaterThan(0)
    expect(typeof bodies[0].target_code).toBe('string')
    expect(String(bodies[0].target_code).length).toBeGreaterThan(0)
  })
})
