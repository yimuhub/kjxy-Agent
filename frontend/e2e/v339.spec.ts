import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.39 指挥官补齐：
 * a. 设置页新增「目标不明确时先问清」「每天自动创建助手上限」，保存后回读一致；
 * b. 切换执行模式会写回会话，刷新后左侧会话行仍显示「指挥官」徽章。
 *
 * 两条都不依赖真实模型；设置改动一律在 finally 里恢复 baseline。
 */

const SETTINGS = '/api/v1/yards/current/settings/commander'
/** 与 UI 登录同一账号（yard_admin/演示科技小院），设置写入的才是同一小院 */
const ADMIN = { username: 'yard_admin', password: 'yard123' }

async function apiToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/v1/auth/login', { data: ADMIN })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.access_token as string
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` }
}

async function getCommander(
  request: APIRequestContext,
  token: string,
): Promise<Record<string, unknown>> {
  const resp = await request.get(SETTINGS, { headers: auth(token) })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data as Record<string, unknown>
}

async function putCommander(
  request: APIRequestContext,
  token: string,
  patch: Record<string, unknown>,
): Promise<void> {
  const resp = await request.put(SETTINGS, { headers: auth(token), data: patch })
  expect(resp.ok()).toBeTruthy()
}

async function openCommanderTab(page: Page) {
  await page.goto('/settings')
  await page.locator('.el-tabs__item', { hasText: '指挥官' }).first().click()
  await expect(page.locator('.settings-commander')).toBeVisible()
}

test.describe('指挥官设置（v3.39）', () => {
  test('澄清开关与每日自动建助手上限可见，保存后回读一致', async ({ page, request }) => {
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    const baselineLimit = Number(baseline.daily_agent_limit ?? 10)
    // 目标值优先 12；baseline 恰好是 12 时换 13，保证「保存后回读」确实验证了写入
    const target = baselineLimit === 12 ? 13 : 12

    try {
      await login(page)
      await openCommanderTab(page)

      const section = page.locator('.settings-commander')
      await expect(section.getByText('目标不明确时先问清', { exact: true })).toBeVisible()
      await expect(section.getByText('每天自动创建助手上限', { exact: true })).toBeVisible()

      const limitItem = section.locator('.el-form-item', { hasText: '每天自动创建助手上限' })
      const input = limitItem.locator('.el-input-number input')
      await input.fill(String(target))
      await input.press('Tab')

      await section.getByRole('button', { name: '保存' }).click()
      await expect(
        page.locator('.el-message').filter({ hasText: '指挥官设置已保存' }).first(),
      ).toBeVisible()

      const saved = await getCommander(request, token)
      expect(Number(saved.daily_agent_limit)).toBe(target)

      // 刷新后表单从服务端回读，仍是刚才保存的值
      await openCommanderTab(page)
      await expect(
        page
          .locator('.settings-commander .el-form-item', { hasText: '每天自动创建助手上限' })
          .locator('.el-input-number input'),
      ).toHaveValue(String(target))
    } finally {
      await putCommander(request, token, {
        daily_agent_limit: baselineLimit,
        clarify_enabled: Boolean(baseline.clarify_enabled ?? false),
      })
    }
  })
})

test.describe('会话指挥官徽章（v3.39）', () => {
  test('切换指挥官写回会话，刷新后左侧会话行显示徽章', async ({ page, request }) => {
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    // 指挥官栏只在启用时出现；测试结束恢复原值
    if (!baseline.enabled) await putCommander(request, token, { enabled: true })

    try {
      await login(page)
      await page.goto('/chat')
      await page.locator('.new-btn').click()
      await expect(page.locator('.chat-empty')).toBeVisible()

      const bar = page.locator('.exec-seg')
      await expect(bar).toBeVisible()
      const [patch] = await Promise.all([
        page.waitForResponse(
          resp =>
            resp.request().method() === 'PATCH' &&
            /\/api\/v1\/chat\/sessions\/[^/]+$/.test(new URL(resp.url()).pathname),
          { timeout: 20_000 },
        ),
        bar.getByText('指挥官', { exact: true }).click(),
      ])
      expect(patch.ok()).toBeTruthy()
      // 写回的 body 必须带 execution_mode=commander（左侧徽章读的是服务端事实）
      expect((patch.request().postDataJSON() as { execution_mode?: string }).execution_mode).toBe(
        'commander',
      )
      await expect(page.locator('.composer-caption')).toContainText('一句话说清目标即可')

      // 记下刚建的会话，刷新后只断言这一行的徽章（不受历史会话里其他指挥官行干扰）
      const sessionId = await page
        .locator('.session-item.active')
        .first()
        .getAttribute('data-session-id')

      // 刷新页面：会话列表重新从服务端拉取，徽章仍应准确
      await page.reload()
      await expect(page.locator('.session-list').first()).toBeVisible()
      const row = sessionId
        ? page.locator(`.session-item[data-session-id="${sessionId}"]`)
        : page.locator('.session-item').first()
      await expect(row).toBeVisible({ timeout: 20_000 })
      const badge = row.locator('.s-kind--commander')
      await expect(badge).toBeVisible({ timeout: 20_000 })
      await expect(badge).toHaveText('指挥官')
    } finally {
      await putCommander(request, token, {
        enabled: Boolean(baseline.enabled ?? true),
      })
    }
  })
})

test.describe('计划卡编辑（v3.39）', () => {
  test('改指令并删除子任务，保存后回显新计划', async ({ page, request }) => {
    test.setTimeout(240_000)
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    await putCommander(request, token, {
      enabled: true,
      preview_plan: true,
      default_mode: false,
      clarify_enabled: false,
      auto_create_agent: true,
      max_parallel: 3,
      max_subtasks: 3,
      subtask_timeout_seconds: 90,
      run_timeout_seconds: 240,
    })

    try {
      await login(page)
      await page.goto('/chat')
      await page.locator('.new-btn').click()
      await expect(page.locator('.chat-empty')).toBeVisible()

      const bar = page.locator('.commander-bar')
      await bar.getByText('指挥官', { exact: true }).click()
      await page
        .locator('textarea')
        .fill('为春耕生产做准备：列出 3 项农资清单，再给出灌溉建议')
      await page.locator('textarea').press('Enter')

      const plan = page.locator('.commander-plan')
      await expect(plan).toBeVisible({ timeout: 180_000 })
      const rows = plan.locator('.subtask')
      const before = await rows.count()
      expect(before).toBeGreaterThanOrEqual(2)

      // 改第一条指令
      await rows.first().getByRole('button', { name: '编辑' }).click()
      const edited = '列出春耕必需的 3 项农资，并逐项标注用途与用量'
      await rows.first().locator('.edit-main textarea').fill(edited)
      // 删除最后一条（至少保留 1 条）
      await rows.last().getByRole('button', { name: '删除' }).click()
      await plan.getByRole('button', { name: '保存修改' }).click()
      await expect(
        page.locator('.el-message').filter({ hasText: '计划已更新' }).first(),
      ).toBeVisible({ timeout: 30_000 })

      await expect(rows.first()).toContainText(edited)
      await expect(rows).toHaveCount(before - 1)
    } finally {
      await putCommander(request, token, {
        enabled: Boolean(baseline.enabled ?? true),
        preview_plan: Boolean(baseline.preview_plan ?? true),
        default_mode: Boolean(baseline.default_mode ?? false),
        clarify_enabled: Boolean(baseline.clarify_enabled ?? false),
        auto_create_agent: Boolean(baseline.auto_create_agent ?? true),
        max_parallel: Number(baseline.max_parallel ?? 3),
        max_subtasks: Number(baseline.max_subtasks ?? 6),
        subtask_timeout_seconds: Number(baseline.subtask_timeout_seconds ?? 180),
        run_timeout_seconds: Number(baseline.run_timeout_seconds ?? 600),
      })
    }
  })
})
