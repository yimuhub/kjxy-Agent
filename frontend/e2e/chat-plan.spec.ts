import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.14 对话内多源方案：「写一份…方案」当场给出可执行的完整方案，
 * 而不是一句「已生成文档」；正文按 Markdown 分节显示，缺章如实标注，可导出 Word、可转班组会诊。
 */
test.describe('对话内完整方案合成', () => {
  test('方案消息带多源卡片：分节正文、取证统计、导出与转会诊入口', async ({ page }) => {
    test.setTimeout(300_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()

    await page.getByRole('textbox', { name: '对话输入' }).fill('写一份小麦赤霉病的防治方案')
    await page.getByRole('textbox', { name: '对话输入' }).press('Enter')

    const answer = page.locator(
      '.msg-row.assistant .bubble.assistant:not(.typing):not(.live-steps)',
    )
    await expect(answer).toBeVisible({ timeout: 240_000 })
    // 正文按章节渲染成标题（不是「已生成文档」那一句，也不是裸的 ## 文本）
    await expect(answer.locator('h2').first()).toBeVisible()
    await expect(answer).not.toContainText('已生成Word 文档')

    // 徽章来自后端：多源方案（绿色）
    await expect(page.locator('.msg-badge').last()).toContainText('多源方案')

    const card = page.locator('.plan-card').last()
    await expect(card).toBeVisible()
    // 取证统计：知识库/联网（或"未联网"的原因）都要看得见
    await expect(card.locator('.counts')).toContainText('知识库')
    await expect(card.getByRole('button', { name: '导出为文档' })).toBeVisible()
    await expect(card.getByRole('button', { name: '转班组会诊' })).toBeVisible()

    // 一问一答：乐观消息不与后端消息重复
    await expect(page.locator('.msg-row.user')).toHaveCount(1)

    // 导出：分节正文 → 后端文档端点 → 浏览器拿到 .docx
    const downloadPromise = page.waitForEvent('download', { timeout: 60_000 })
    await card.getByRole('button', { name: '导出为文档' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toContain('.docx')

    // 转会诊：弹窗列出可用班组（没有班组时给出明确指引）
    await card.getByRole('button', { name: '转班组会诊' }).click()
    await expect(page.getByRole('dialog', { name: '转班组会诊' })).toBeVisible()
    await page.getByRole('button', { name: '取消' }).last().click()

    expect(errors).toEqual([])
  })

  test('点名方案格式或指定助手时不误入方案链路', async ({ page }) => {
    test.setTimeout(300_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()

    // 显式点名 Word 格式：仍按文档链路产出附件，不弹方案卡片
    await page
      .getByRole('textbox', { name: '对话输入' })
      .fill('写一份小麦赤霉病的防治方案，用 Word 格式')
    await page.getByRole('textbox', { name: '对话输入' }).press('Enter')
    await expect(
      page.locator('.msg-row.assistant .bubble.assistant:not(.typing):not(.live-steps)'),
    ).toBeVisible({ timeout: 240_000 })
    await expect(page.locator('.plan-card')).toHaveCount(0)

    expect(errors).toEqual([])
  })
})
