import { expect, test } from '@playwright/test'
import { login } from './helpers'

const OVERVIEW = {
  environment: 'standard',
  environments: [
    { key: 'trial', label: '试点试用', description: '50 万 Token/月', policies: [] },
    { key: 'standard', label: '标准运行', description: '200 万 Token/月', policies: [] },
    { key: 'production', label: '生产高负载', description: '1000 万 Token/月', policies: [] },
  ],
  legacy_calls_mode: 'warn',
  default_grant_days: 7,
  policies: [
    {
      id: 'p-yard',
      scope_type: 'yard',
      scope_type_label: '全小院',
      scope_key: '',
      metric: 'tokens',
      metric_label: 'Token 用量',
      window: 'month',
      window_label: '每月',
      limit_value: 2000000,
      granted: 0,
      effective_limit: 2000000,
      used: 961000,
      remaining: 1039000,
      usage_percent: 48.05,
      on_exceed: 'approve',
      on_exceed_label: '拦截并允许申请',
      enabled: true,
      source: 'template',
      note: null,
      window_start: '2026-10',
    },
    {
      id: 'p-agent',
      scope_type: 'agent',
      scope_type_label: '单个 Agent',
      scope_key: 'gen_diag',
      metric: 'tokens',
      metric_label: 'Token 用量',
      window: 'day',
      window_label: '每天',
      limit_value: 200000,
      granted: 50000,
      effective_limit: 250000,
      used: 120000,
      remaining: 130000,
      usage_percent: 48,
      on_exceed: 'block',
      on_exceed_label: '直接拦截',
      enabled: true,
      source: 'manual',
      note: '诊断助手日常巡检',
      window_start: '2026-10-30',
    },
  ],
  pending_requests: 1,
  options: {
    agents: [{ code: 'gen_diag', name: '诊断助手', group: '诊断' }],
    models: ['deepseek-chat'],
  },
  can_manage: true,
  can_approve: true,
  can_request: true,
}

const REQUESTS = {
  items: [
    {
      id: 'req-1',
      policy_id: 'p-agent',
      scope_type: 'agent',
      scope_type_label: '单个 Agent',
      scope_key: 'gen_diag',
      metric: 'tokens',
      metric_label: 'Token 用量',
      window: 'day',
      window_label: '每天',
      current_limit: 200000,
      requested_limit: 500000,
      reason: '月底集中出诊断报告',
      status: 'pending',
      requester_id: 'u-op',
      requester_name: '王技术员',
      valid_days: 7,
      created_at: '2026-10-30T10:00:00Z',
    },
  ],
  can_approve: true,
}

test.describe('v3.32 分级额度：策略、申请与审批', () => {
  test('额度策略按 Agent 展示并可按分组新增', async ({ page }) => {
    await login(page)
    let saved: Record<string, unknown> | null = null
    await page.route('**/api/v1/quota/overview', route =>
      route.fulfill({ json: { data: OVERVIEW, request_id: 'e2e' } }),
    )
    await page.route('**/api/v1/quota/policies', async route => {
      if (route.request().method() === 'PUT') {
        saved = route.request().postDataJSON() as Record<string, unknown>
        await route.fulfill({ json: { data: OVERVIEW.policies[0], request_id: 'e2e' } })
        return
      }
      await route.fallback()
    })

    await page.goto('/quota')
    await page.getByRole('tab', { name: '额度策略' }).click()

    await expect(page.getByText('额度环境')).toBeVisible()
    await expect(page.getByText('全小院总额度')).toBeVisible()
    await expect(page.getByText('含临时额度 +50.0K')).toBeVisible()

    await page.getByRole('button', { name: '新增额度' }).nth(0).click()
    const dialog = page.getByRole('dialog', { name: '新增额度' })
    await expect(dialog).toBeVisible()
    await dialog.locator('.el-select').first().click()
    await page.getByRole('option', { name: '单个 Agent' }).click()
    await dialog.locator('.el-select').nth(1).click()
    await page.getByRole('option', { name: '诊断助手（诊断）' }).click()
    const limit = dialog.locator('.el-input-number input')
    await limit.fill('300000')
    await limit.blur()
    await dialog.getByRole('button', { name: '保存' }).click()

    await expect.poll(() => saved?.scope_type).toBe('agent')
    expect(saved?.scope_key).toBe('gen_diag')
    expect(saved?.metric).toBe('tokens')
    expect(saved?.limit_value).toBe(300000)
  })

  test('额度申请可审批并展示临时额度结果', async ({ page }) => {
    await login(page)
    let approved: Record<string, unknown> | null = null
    await page.route('**/api/v1/quota/overview', route =>
      route.fulfill({ json: { data: OVERVIEW, request_id: 'e2e' } }),
    )
    await page.route('**/api/v1/quota/requests', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: { data: REQUESTS, request_id: 'e2e' } })
        return
      }
      await route.fallback()
    })
    await page.route('**/api/v1/quota/requests/*/approve', async route => {
      approved = route.request().postDataJSON() as Record<string, unknown>
      await route.fulfill({
        json: {
          data: {
            request: { ...REQUESTS.items[0], status: 'approved' },
            grant: { id: 'g-1', amount: 300000, expires_at: '2026-11-06T10:00:00Z' },
          },
          request_id: 'e2e',
        },
      })
    })

    await page.goto('/quota')
    await page.getByRole('tab', { name: '额度申请' }).click()

    await expect(page.getByText('月底集中出诊断报告')).toBeVisible()
    await expect(page.getByText('200.0K →')).toBeVisible()

    await page.getByRole('button', { name: '通过' }).click()
    const dialog = page.getByRole('dialog', { name: '批准提额' })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: '批准' }).click()

    await expect.poll(() => approved?.approved_limit).toBe(500000)
    await expect(page.getByText(/已批准/)).toBeVisible()
  })
})
