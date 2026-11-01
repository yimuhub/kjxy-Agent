import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.40 指挥官自主补救：
 * a. 设置页新增「失败后自主补救」「最多自主尝试轮数」，保存后服务端回读一致；
 * b. 执行模式提示改成「自己想办法完成」的口径。
 *
 * 设置改动一律在 finally 里恢复 baseline；不依赖真实模型。
 */

const SETTINGS = '/api/v1/yards/current/settings/commander'
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

test.describe('指挥官自主补救设置（v3.40）', () => {
  test('补救开关与轮数可见，保存后回读一致', async ({ page, request }) => {
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    const baselineRounds = Number(baseline.remediate_rounds ?? 5)
    const target = baselineRounds === 4 ? 5 : 4

    try {
      await login(page)
      await openCommanderTab(page)

      const section = page.locator('.settings-commander')
      await expect(section.getByText('失败后自主补救', { exact: true })).toBeVisible()
      await expect(section.getByText('最多自主尝试轮数', { exact: true })).toBeVisible()

      // 轮数输入框在补救关闭时禁用：先把开关打开
      const remedyItem = section.locator('.el-form-item', { hasText: '失败后自主补救' })
      const switchEl = remedyItem.locator('.el-switch')
      if (!(await switchEl.evaluate(el => el.classList.contains('is-checked')))) {
        await switchEl.click()
      }

      const roundsItem = section.locator('.el-form-item', { hasText: '最多自主尝试轮数' })
      const roundsInput = roundsItem.locator('.el-input-number input')
      await roundsInput.fill(String(target))
      await roundsInput.press('Tab')

      await section.getByRole('button', { name: '保存' }).click()
      await expect(
        page.locator('.el-message').filter({ hasText: '指挥官设置已保存' }).first(),
      ).toBeVisible()

      const saved = await getCommander(request, token)
      expect(Number(saved.remediate_rounds)).toBe(target)
      expect(Boolean(saved.auto_remediate)).toBe(true)

      await openCommanderTab(page)
      await expect(
        page
          .locator('.settings-commander .el-form-item', { hasText: '最多自主尝试轮数' })
          .locator('.el-input-number input'),
      ).toHaveValue(String(target))
    } finally {
      await putCommander(request, token, {
        auto_remediate: Boolean(baseline.auto_remediate ?? true),
        remediate_rounds: baselineRounds,
      })
    }
  })
})

test.describe('指挥官模式提示（v3.40）', () => {
  test('切到指挥官后提示「自己把任务做完」而非只派任务', async ({ page, request }) => {
    const token = await apiToken(request)
    const baseline = await getCommander(request, token)
    if (!baseline.enabled) await putCommander(request, token, { enabled: true })

    try {
      await login(page)
      await page.goto('/chat')
      await page.locator('.new-btn').click()
      await expect(page.locator('.chat-empty')).toBeVisible()

      const bar = page.locator('.exec-seg')
      await expect(bar).toBeVisible()
      await bar.getByText('指挥官', { exact: true }).click()
      await expect(page.locator('.composer-caption')).toContainText('自己拆任务、找助手、调工具')
    } finally {
      await putCommander(request, token, {
        enabled: Boolean(baseline.enabled ?? true),
      })
    }
  })
})
