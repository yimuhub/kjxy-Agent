import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** v3.13 信息架构收口：概览改成今日行动板、问答并入对话、回答反馈回流到专家审核 */

test.describe('今日行动板（原概览）', () => {
  test('管理员进门看到今天的待办与可点数字，而不是平台统计', async ({ page }) => {
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await expect(page.getByRole('heading', { name: '今日工作台' })).toBeVisible()
    // 问候语带用户名，摘要一句话说清今天要处理什么
    await expect(page.locator('.dash-sub')).toContainText('小院管理员')
    const quick = page.locator('.quick-actions')
    await expect(quick.getByRole('button', { name: '记一笔农事' })).toBeVisible()
    await expect(quick.getByRole('button', { name: '拍照诊断' })).toBeVisible()
    // 改版前的平台总览块不应再出现在小院首页
    await expect(page.getByText('平台总览', { exact: true })).toHaveCount(0)

    // 数字卡点进对应列表
    await page.getByRole('button', { name: /今日农事记录/ }).click()
    await expect(page).toHaveURL(/\/farm$/)

    await page.goto('/')
    await page.getByRole('button', { name: /待处理工单|我的待办工单/ }).click()
    await expect(page).toHaveURL(/\/work-orders$/)

    // 分区面板按角色取数：管理员看的是待专家复核的诊断
    await expect(page.locator('.panel').filter({ hasText: '田间运行' })).toBeVisible()
    expect(errors).toEqual([])
  })

  test('科研人员登录后直达对话，概览仍可返回', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('operator')
    await page.locator('input[autocomplete="current-password"]').fill('operator123')
    await page.getByRole('button', { name: '登录', exact: true }).click()

    // 一线角色不再先落数据页，进门即可开聊
    await expect(page).toHaveURL(/\/chat$/)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '今日工作台' })).toBeVisible()
    await expect(page.getByRole('button', { name: '我的待办工单' })).toBeVisible()
  })
})

test.describe('问答并入对话', () => {
  test('侧边菜单不再有独立问答入口', async ({ page }) => {
    await login(page)
    await expect(page.getByRole('menuitem', { name: '问答', exact: true })).toHaveCount(0)
    await expect(page.getByRole('menuitem', { name: '对话', exact: true })).toBeVisible()
  })

  test('知识问答模式在对话内可用：只依据知识库、可调解检索条数', async ({ page }) => {
    test.setTimeout(120_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()

    await page.getByRole('button', { name: '知识问答', exact: true }).click()
    await expect(page.getByRole('spinbutton', { name: '知识库检索条数' })).toBeVisible()
    // 问答模式收敛点名与班组，避免和通用对话重复
    await expect(page.locator('.group-band')).toHaveCount(0)

    await page.getByRole('textbox', { name: '对话输入' }).fill('水稻分蘖期怎么管水？')
    await page.getByRole('textbox', { name: '对话输入' }).press('Enter')

    const answer = page.locator('.msg-row.assistant .bubble.assistant:not(.typing):not(.live-steps)')
    await expect(answer).toBeVisible({ timeout: 90_000 })
    // 一问一答：乐观消息与后端消息不得重复出现在流里
    await expect(page.locator('.msg-row.user')).toHaveCount(1)
    // 问答回答一定标注来源，路由徽章显示「知识问答」
    await expect(page.locator('.msg-badge').last()).toContainText('知识问答')
    await expect(page.locator('.msg-citations').last()).toBeVisible()

    expect(errors).toEqual([])
  })
})

test.describe('回答反馈回流', () => {
  test('点赞留档、纠错生成待审核候选并可在工作台计数', async ({ page }) => {
    test.setTimeout(180_000)
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    const stamp = Date.now().toString(36)
    const correction = `演示纠错 ${stamp}：分蘖期应保持浅水勤灌，达到计划穗数八成时及时晒田。`

    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()
    await page.getByRole('textbox', { name: '对话输入' }).fill('水稻分蘖期田间水位怎么管？')
    await page.getByRole('textbox', { name: '对话输入' }).press('Enter')

    const answer = page.locator('.msg-row.assistant .bubble.assistant:not(.typing):not(.live-steps)')
    await expect(answer).toBeVisible({ timeout: 120_000 })
    await expect(page.locator('.msg-row.user')).toHaveCount(1)

    const useful = page.getByRole('button', { name: '反馈这条回答有用' })
    await useful.click()
    await expect(page.locator('.el-message--success').first()).toContainText('已记录')
    await expect(
      page.getByRole('button', { name: '已反馈：这条回答有用' }),
    ).toBeVisible()

    // 点「有问题」：先落一条 down 评分，再展开纠错输入
    await page.getByRole('button', { name: '反馈这条回答有问题并可补充正确说法' }).click()
    const box = page.getByRole('textbox', { name: '这条回答的纠错内容' })
    await expect(box).toBeVisible()
    await expect(page.getByRole('button', { name: '提交纠错' })).toBeDisabled()

    await box.fill(correction)
    await page.getByRole('button', { name: '提交纠错' }).click()
    await expect(page.locator('.el-message--success').last()).toContainText('待审核候选')
    await expect(
      page.getByRole('button', { name: '反馈这条回答有问题并可补充正确说法' }),
    ).toContainText('已提交纠错')

    // 闭环：纠错进入专家审核队列，标题带原问题便于对号
    await page.goto('/review')
    await expect(page.locator('.el-table').first()).toContainText(correction.slice(0, 18))

    expect(errors).toEqual([])
  })
})
