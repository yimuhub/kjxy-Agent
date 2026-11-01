import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.38 指挥官 Agent：
 * 1. 设置页可改并发、保存后回读一致（后端是唯一事实源）；
 * 2. 对话页可切「指挥官」，一句话目标生成计划卡，确认后进入执行态。
 *
 * 计划生成/执行都依赖真实模型，往返时长浮动，按实测放宽超时。
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

test.describe('指挥官设置', () => {
  test('并行数可选 3/5/8/10/自定义，保存后回读一致', async ({ page, request }) => {
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    try {
      await login(page)
      await openCommanderTab(page)

      const five = page.locator('.settings-commander .el-radio-button', { hasText: '5' }).first()
      await five.click()
      await page.locator('.settings-commander').getByRole('button', { name: '保存' }).click()
      await expect(
        page.locator('.el-message').filter({ hasText: '指挥官设置已保存' }).first(),
      ).toBeVisible()

      const saved = await getCommander(request, token)
      expect(saved.max_parallel).toBe(5)

      // 刷新后表单从服务端回读，仍是刚才保存的值
      await openCommanderTab(page)
      await expect(
        page.locator('.settings-commander .el-radio-button.is-active', { hasText: '5' }),
      ).toBeVisible()
    } finally {
      await putCommander(request, token, {
        max_parallel: Number(baseline.max_parallel ?? 3),
        enabled: Boolean(baseline.enabled ?? true),
        preview_plan: Boolean(baseline.preview_plan ?? true),
        default_mode: Boolean(baseline.default_mode ?? false),
      })
    }
  })
})

test.describe('对话内指挥官编排', () => {
  test('切换到指挥官后生成计划卡，确认后进入执行态', async ({ page, request }) => {
    test.setTimeout(240_000)
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    await putCommander(request, token, {
      enabled: true,
      preview_plan: true,
      default_mode: false,
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
      await expect(bar).toBeVisible()
      await bar.getByText('指挥官', { exact: true }).click()
      await expect(bar).toContainText('一句话说清目标即可')

      await page
        .locator('textarea')
        .fill('为春耕生产做准备：列出需要检查的农资清单，再给出灌溉和施肥建议')
      await page.locator('textarea').press('Enter')

      // 计划卡：先预览，不自动开跑
      const plan = page.locator('.commander-plan')
      await expect(plan).toBeVisible({ timeout: 180_000 })
      await expect(plan).toContainText('指挥官计划')
      await expect(plan.locator('.subtask').first()).toBeVisible()

      const startBtn = plan.getByRole('button', { name: '开始执行' })
      await expect(startBtn).toBeVisible()
      await startBtn.click()

      // 执行开始后卡片内嵌运行视图（状态轮询、子任务实时更新）
      await expect(page.locator('.commander-run')).toBeVisible({ timeout: 60_000 })
      await expect(page.locator('.commander-run')).toContainText('指挥官执行')
    } finally {
      await putCommander(request, token, {
        enabled: Boolean(baseline.enabled ?? true),
        preview_plan: Boolean(baseline.preview_plan ?? true),
        default_mode: Boolean(baseline.default_mode ?? false),
        auto_create_agent: Boolean(baseline.auto_create_agent ?? true),
        max_parallel: Number(baseline.max_parallel ?? 3),
        max_subtasks: Number(baseline.max_subtasks ?? 6),
        subtask_timeout_seconds: Number(baseline.subtask_timeout_seconds ?? 180),
        run_timeout_seconds: Number(baseline.run_timeout_seconds ?? 600),
      })
    }
  })
})
