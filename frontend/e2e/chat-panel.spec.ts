import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { login, sessionTotal } from './helpers'

/**
 * ChatPanel 多实例隔离验证（配合 /chat 顶部「双面板（测试）」脚手架开关）。
 *
 * 这一波重构的目标：对话状态从全局单例 store 改为 useChatStore(scope) 实例化，
 * 让「主对话 + 弹出使用对话」能并存。这里用同屏两个真实面板实例证明：
 * 弹出实例发送期间，主实例的消息列表、sending 态、草稿与会话归属都不被污染。
 *
 * 真正的「使用」对话框（专业助手）在下一波交付，届时删除 ChatView 里的脚手架分支。
 * 注意：脚手架只在 DEV 构建里渲染，即 playwright 的 webServer（npm run dev）。
 */

/** 按 data-panel 取某一个面板实例（ChatPanel 的 panelId prop 落到 DOM 上） */
function panel(page: Page, panelId: string): Locator {
  return page.locator(`.chat-panel[data-panel="${panelId}"]`)
}

/** 在指定面板内发一条消息（Enter 发送，与主对话同一交互） */
async function sendIn(target: Locator, text: string) {
  const box = target.locator('textarea')
  await box.click()
  await box.fill(text)
  await box.press('Enter')
}

/** 只填字不发送，用于断言两个实例的草稿互不可见 */
async function typeDraft(target: Locator, text: string) {
  await target.locator('textarea').fill(text)
}

/** 面板内已落库的回答气泡（排除发送中的实时步骤条与三点波浪） */
function answered(target: Locator): Locator {
  return target.locator('.msg-row.assistant .bubble:not(.live-steps):not(.typing)')
}

test.describe('对话面板多实例隔离', () => {
  test.beforeEach(async ({ page }) => {
    // 弹卡固定定位在右上，视口给足高度，避免与主对话输入区重叠
    await page.setViewportSize({ width: 1440, height: 900 })
  })

  test('分段控件开合弹出面板，两个实例各持自己的草稿', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()

    const main = panel(page, 'main')
    await expect(main).toBeVisible()
    await expect(main.locator('.chat-empty')).toBeVisible()
    // 未打开脚手架时只有主对话一个面板，页面行为与改造前一致
    await expect(panel(page, 'popup')).toHaveCount(0)

    // 开合控件用 el-segmented（radiogroup 语义由组件自带）
    const segmented = page.locator('.t-harness')
    await expect(segmented).toHaveAttribute('role', 'radiogroup')
    const dual = page.getByRole('radio', { name: '双面板（测试）' })
    await segmented.locator('.el-segmented__item', { hasText: '双面板' }).click()
    await expect(dual).toBeChecked()

    const popup = panel(page, 'popup')
    await expect(popup).toBeVisible()
    await expect(page.locator('.chat-panel')).toHaveCount(2)
    await expect(popup.locator('.chat-empty')).toBeVisible()

    await typeDraft(main, '主对话草稿')
    await expect(main.locator('textarea')).toHaveValue('主对话草稿')
    await expect(popup.locator('textarea')).toHaveValue('')

    // 弹出实例的建议卡只填它自己的输入框
    await popup.locator('.suggest-card').first().click()
    const popupDraft = await popup.locator('textarea').inputValue()
    expect(popupDraft.length).toBeGreaterThan(0)
    await expect(main.locator('textarea')).toHaveValue('主对话草稿')

    // 关掉脚手架：只剩主对话，草稿仍在
    await page.locator('.harness-close').click()
    await expect(popup).toHaveCount(0)
    await expect(main.locator('textarea')).toHaveValue('主对话草稿')
  })

  test('弹出实例发送期间，主实例的消息与 sending 不受影响', async ({ page }) => {
    // 两轮真实模型往返（实测单轮 ~33s），超时按实测放宽
    test.setTimeout(300_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await page.goto('/chat?panel-harness=1')

    const main = panel(page, 'main')
    const popup = panel(page, 'popup')
    await expect(main).toBeVisible()
    await expect(popup).toBeVisible()

    // 基线：主对话新建会话并完整跑一轮，拿到「已有内容」的状态
    await page.locator('.new-btn').click()
    await expect(main.locator('.chat-empty')).toBeVisible()
    await sendIn(main, '水稻叶片发黄可能是什么原因？')
    await expect(answered(main).first()).toBeVisible({ timeout: 150_000 })
    await expect(answered(main).first()).not.toBeEmpty()
    // 等首轮完全收尾：步骤行/光标占位会随流式结束合并，提前取基线会数到临时行
    await expect(main.locator('.send-btn')).not.toHaveClass(/stop/, { timeout: 150_000 })
    await expect(main.locator('.live-steps')).toHaveCount(0)

    const mainRows = await main.locator('.msg-row').count()
    expect(mainRows).toBeGreaterThanOrEqual(2)
    const mainTitle = (await page.locator('.t-title').innerText()).trim()
    // 列表窗口化后 DOM 行数封顶，条目数改读容器上的模型总数
    const mainSessionCount = await sessionTotal(page)

    // 主对话留一段草稿：若两实例共享 store，主面板会被弹出实例的 sending 拖进禁用态
    await typeDraft(main, '主对话不该被弹出实例打断的草稿')

    // 弹出实例开始发送（独立 scope，自己建会话）
    await sendIn(popup, '帮我梳理一下小麦赤霉病的防治要点')
    await expect(popup.locator('.send-btn')).toBeDisabled()
    await expect(popup.locator('.bubble.typing, .live-steps').first()).toBeVisible()

    // 隔离断言：主实例仍处空闲态，消息一字未动
    await expect(main.locator('.send-btn')).toBeEnabled()
    await expect(main.locator('.msg-row')).toHaveCount(mainRows)
    await expect(main.locator('.bubble.typing')).toHaveCount(0)
    await expect(main.locator('.live-steps')).toHaveCount(0)
    await expect(main.locator('.stream-warn')).toHaveCount(0)
    await expect(main.locator('textarea')).toHaveValue('主对话不该被弹出实例打断的草稿')
    // 会话归属不串：顶部标题仍是主对话的会话，侧栏条目数不变
    await expect(page.locator('.t-title')).toHaveText(mainTitle)
    await expect.poll(() => sessionTotal(page)).toBe(mainSessionCount)
    // 弹出实例自己建了会话，但只写进它自己的 store
    await expect(page.locator('[data-harness-session]')).not.toHaveText('未开始会话')

    // 弹出实例的回答只落在它自己的面板里
    await expect(answered(popup).first()).toBeVisible({ timeout: 150_000 })
    await expect(answered(popup).first()).not.toBeEmpty()
    // 发送态结束：主实例全程没被写过消息
    await expect(popup.locator('.send-btn')).not.toHaveClass(/stop/)
    await expect(main.locator('.msg-row')).toHaveCount(mainRows)
    await expect(page.locator('.t-title')).toHaveText(mainTitle)
    await expect(main.locator('textarea')).toHaveValue('主对话不该被弹出实例打断的草稿')

    expect(errors).toEqual([])
  })
})
