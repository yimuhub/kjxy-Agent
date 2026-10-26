import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** 附件样本：纯文本灌溉记录，用于验证"上传 → 模型读取 → 回答"闭环 */
const NOTE = [
  '灌溉记录 2026-10-20',
  '土壤墒情 18%，水稻处于孕穗期。',
  '近三天无降雨，田间水位偏低，建议今日傍晚补水 15mm。',
].join('\n')

/** 新建空会话：隔离历史消息，断言不被既有会话干扰 */
async function newChat(page: import('@playwright/test').Page) {
  await page.goto('/chat')
  await page.locator('.new-btn').click()
  await expect(page.locator('.chat-empty')).toBeVisible()
}

test.describe('对话附件与能力生成', () => {
  test('上传附件后实时显示步骤，回答落库可回看', async ({ page }) => {
    // 问答链路要走真实模型，往返可达 30s+，超时按实测放宽
    test.setTimeout(120_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await newChat(page)

    // 输入框左侧的附件按钮 + 待发附件 chip
    const attachBtn = page.locator('.attach-btn')
    await expect(attachBtn).toBeVisible()
    await expect(attachBtn).toHaveAttribute('title', '上传附件（图片或文档）')

    await page.locator('input.file-input').setInputFiles({
      name: 'irrigation-note.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(NOTE, 'utf-8'),
    })
    await expect(page.locator('.att-chip')).toContainText('irrigation-note.txt')

    // 发送后：实时步骤条出现 → 历史步骤可展开 → 回答落库
    await page.locator('textarea').fill('这份记录说明今天需要浇水吗？')
    await page.locator('textarea').press('Enter')

    await expect(page.locator('.live-steps')).toBeVisible()
    await expect(page.locator('.live-steps')).toContainText('正在阅读附件')

    await expect(page.locator('.msg-row.user .att-tag')).toHaveText(
      'irrigation-note.txt',
    )

    // 回答落库后实时条收起，历史气泡可展开"过程 · N 步"
    const answer = page.locator('.msg-row.assistant .bubble:not(.live-steps)')
    await expect(answer).toBeVisible({ timeout: 90_000 })
    await expect(answer).not.toBeEmpty()
    await expect(page.locator('.live-steps')).toHaveCount(0, { timeout: 15_000 })

    const toggle = page.locator('.msg-row.assistant .toggle')
    await expect(toggle).toContainText('过程')
    await toggle.click()
    await expect(page.locator('.msg-row.assistant .step').first()).toBeVisible()

    expect(errors).toEqual([])
  })

  test('对话内生成能力草稿并发布，卡片可渲染可操作', async ({ page }) => {
    // 能力规格由模型生成，往返时长随负载浮动
    test.setTimeout(120_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await newChat(page)

    await page
      .locator('textarea')
      .fill('创建一个「农机维修问答」助手，以后专门用它')
    await page.locator('textarea').press('Enter')

    // 能力卡片：类型、能力名、编码、草稿状态与发布动作
    const card = page.locator('.card.capability').last()
    await expect(card).toBeVisible({ timeout: 60_000 })
    await expect(card.locator('.kind')).toHaveText('能力卡片')
    await expect(card.locator('.title')).not.toBeEmpty()
    await expect(card.locator('.kv').first()).toContainText('编码')
    await expect(card).toContainText('状态')

    // 路由徽章：对话直办（local），未知取值不得让整条消息渲染中断
    await expect(page.locator('.msg-badge .el-tag').last()).toHaveText('对话直办')

    const publish = card.getByRole('button', { name: '发布' })
    await expect(publish).toBeVisible()
    await publish.click()
    await expect(card).toContainText('已发布')

    expect(errors).toEqual([])
  })
})
