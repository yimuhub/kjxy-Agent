import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/** v3.23 设备数据智能：立即分析出规则发现、概览洞察可见、证据不足如实提示、设置页可配。 */

async function apiToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.access_token as string
}

function auth(token: string) {
  return { Authorization: `Bearer ${token}` }
}

function unique(prefix: string): string {
  return `${prefix}${Date.now().toString().slice(-6)}`
}

async function createDevice(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<{ id: string; device_token: string }> {
  const resp = await request.post('/api/v1/devices', {
    headers: auth(token),
    data: { name, device_type: 'weather', source_type: 'webhook' },
  })
  expect(resp.ok()).toBeTruthy()
  const data = (await resp.json()).data
  return { id: data.id as string, device_token: data.device_token as string }
}

async function ingestHour(
  request: APIRequestContext,
  deviceId: string,
  deviceToken: string,
  hoursAgo: number,
  data: Record<string, number>,
): Promise<void> {
  const reported_at = new Date(Date.now() - hoursAgo * 3600_000).toISOString()
  const resp = await request.post(`/api/v1/devices/${deviceId}/ingest`, {
    headers: { 'X-Device-Token': deviceToken },
    data: { data, reported_at },
  })
  expect(resp.ok()).toBeTruthy()
}

async function disableLlm(request: APIRequestContext, token: string): Promise<void> {
  const resp = await request.put('/api/v1/yards/current/settings/device_analysis', {
    headers: auth(token),
    data: { explain_with_llm: false },
  })
  expect(resp.ok()).toBeTruthy()
}

test.describe('设备数据智能（v3.23）', () => {
  test('立即分析：规则发现带具体数值，概览出现设备洞察', async ({ page, request }) => {
    const token = await apiToken(request)
    await disableLlm(request, token)
    const name = unique('E2E分析站')
    const device = await createDevice(request, token, name)
    for (let i = 0; i < 12; i += 1) {
      await ingestHour(request, device.id, device.device_token, 11 - i, {
        soil_moisture_20: 35 - i,
        rain: 0,
        humidity: 88 + (i % 3),
        temp: 14.5 + (i % 2) * 0.3,
        light: 3000 + i * 10,
      })
    }

    await login(page, 'admin', 'admin123')
    await page.goto('/devices')
    const card = page.locator('.dev-card', { hasText: name })
    await expect(card).toBeVisible()
    await card.getByRole('button', { name: 'AI 分析' }).click()

    const dialog = page.getByRole('dialog', { name: `AI 分析：${name}` })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: '立即分析' }).click()
    await expect(dialog.getByText(/土壤水分/).first()).toBeVisible()
    await expect(dialog.getByText(/数据质量/).first()).toBeVisible()
    await expect(dialog.getByText(/规则结论/).first()).toBeVisible()

    // 关闭弹窗后卡片可见最近分析摘要
    await dialog.locator('.el-dialog__headerbtn').click()
    await expect(dialog).toBeHidden()
    await expect(card).toContainText('最近分析')

    await page.goto('/')
    const insight = page.locator('.insight-item', { hasText: name })
    await expect(insight).toBeVisible()
    await expect(insight).toContainText('条')
  })

  test('证据不足：如实提示，不产出结论', async ({ page, request }) => {
    const token = await apiToken(request)
    await disableLlm(request, token)
    const name = unique('E2E空站')
    await createDevice(request, token, name)

    await login(page, 'admin', 'admin123')
    await page.goto('/devices')
    const card = page.locator('.dev-card', { hasText: name })
    await card.getByRole('button', { name: 'AI 分析' }).click()
    const dialog = page.getByRole('dialog', { name: `AI 分析：${name}` })
    await expect(dialog).toContainText('还没有分析记录')
    await dialog.getByRole('button', { name: '立即分析' }).click()
    await expect(page.getByText(/没有任何读数，无法分析/).first()).toBeVisible()
    await expect(dialog).toContainText('还没有分析记录')
  })

  test('设置页：设备分析可配（窗口/模型解释/预算）', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=device_analysis')
    const panel = page.getByRole('tabpanel', { name: '设备分析' })
    await expect(panel).toContainText('分析窗口')
    await expect(panel).toContainText('模型解释')
    await expect(panel).toContainText('每日 token 预算')
    await expect(panel).toContainText(/不控制设备/)
  })
})
