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

    // 输入框左侧的附件按钮 + 待发附件 chip（语音按钮与它同款式，按名称取附件按钮）
    const attachBtn = page.getByRole('button', { name: '上传附件', exact: true })
    await expect(attachBtn).toBeVisible()
    await expect(attachBtn).toHaveAttribute('title', '上传附件（图片或文档）')

    await page.locator('input.file-input').setInputFiles({
      name: 'irrigation-note.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(NOTE, 'utf-8'),
    })
    await expect(page.locator('.att-chip')).toContainText('irrigation-note.txt')

    // 发送后：步骤条出现（真实模型下可稳定观察）→ 历史步骤可展开 → 回答落库
    await page.locator('textarea').fill('这份记录说明今天需要浇水吗？')
    await page.locator('textarea').press('Enter')

    // 实时步骤条是生成期间的瞬态 UI：mock 模型毫秒级返回时它可能已经收起，
    // 因此只做软观察；「步骤确实产生并留痕」由下面的历史步骤断言兜住。
    const live = page.locator('.live-steps')
    const sawLive = await live
      .waitFor({ state: 'visible', timeout: 3000 })
      .then(() => true)
      .catch(() => false)
    if (sawLive) await expect(live).toContainText('正在阅读附件')

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
    await expect(page.locator('.msg-row.assistant').last()).toContainText('阅读')

    // 一键复制：提问与回答各有一个复制按钮，点击后短暂显示「已复制」
    const copyBtn = page.locator('.msg-row.assistant .msg-copy')
    await expect(page.locator('.msg-row.user .msg-copy')).toHaveCount(1)
    await expect(copyBtn).toHaveCount(1)
    await copyBtn.click()
    await expect(copyBtn).toContainText('已复制')

    expect(errors).toEqual([])
  })

  test('新建会话按服务端默认模式发送，不继承上一个会话的指挥官模式', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    // 悬浮伙伴的常驻会话就是指挥官模式：新建会话若沿用它的模式，
    // 普通提问会被当成指挥官目标执行（v3.72 修复的串会话缺陷）。
    await expect(page.locator('.session-list').first()).toBeVisible()
    await page.locator('.new-btn').click()
    await expect(page.locator('.chat-empty')).toBeVisible()

    if (!(await page.locator('.exec-seg').isVisible())) {
      test.skip(true, '本环境未启用指挥官，无法验证模式继承')
    }

    const [stream] = await Promise.all([
      page.waitForRequest(
        req => req.method() === 'POST' && req.url().endsWith('/messages/stream'),
      ),
      (async () => {
        await page.getByRole('textbox', { name: '对话输入' }).fill('今天适合巡田吗？')
        await page.getByRole('textbox', { name: '对话输入' }).press('Enter')
      })(),
    ])
    const body = stream.postDataJSON() as { execution_mode?: string }
    expect(body.execution_mode ?? 'standard').toBe('standard')
    await expect(page.locator('.msg-row.assistant').last()).toBeVisible({ timeout: 30_000 })
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

  test('对话内生成 Word 文档并可预览、下载', async ({ page }) => {
    // 文档内容由模型生成，往返时长随负载浮动
    test.setTimeout(150_000)
    const errors: string[] = []
    // 预览在无脚本沙箱 iframe 中渲染：浏览器会主动记录「Blocked script execution」
    // 安全提示——这正是隔离生效的证据，不算页面错误。
    const sandboxNotice = /Blocked script execution in 'about:srcdoc'/
    page.on('console', msg => {
      if (msg.type() === 'error' && !sandboxNotice.test(msg.text())) errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await newChat(page)

    await page.locator('textarea').fill('写一份设备巡检报告，用 Word 格式')
    await page.locator('textarea').press('Enter')

    // 文档卡片：类型、格式徽章、预览/下载动作
    const card = page.locator('.card.document').last()
    await expect(card).toBeVisible({ timeout: 90_000 })
    await expect(card.locator('.kind')).toHaveText('生成文档')
    await expect(card.locator('.fmt')).toHaveText('Word')
    await expect(card.locator('.title')).toContainText('.docx')

    // 在线预览：沙箱 iframe 渲染后端自包含 HTML
    await card.getByRole('button', { name: '预览' }).click()
    const dialog = page.locator('.doc-preview-dialog')
    await expect(dialog).toBeVisible()
    const frame = dialog.locator('iframe.doc-preview-frame')
    await expect(frame).toBeVisible({ timeout: 30_000 })
    await expect(frame).toHaveAttribute('sandbox', '')

    // 下载：浏览器收到 docx 附件
    const downloadPromise = page.waitForEvent('download', { timeout: 30_000 })
    await dialog.getByRole('button', { name: '下载' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toContain('.docx')

    expect(errors).toEqual([])
  })
})
