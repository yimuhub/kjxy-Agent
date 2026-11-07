import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.28 Agent 自动执行：
 * - 系统设置新增「Agent 自动执行」与「消息推送」，可保存并回显；
 * - 台账页可手动跑一轮，按策略落成「待确认 / 已执行」动作；
 * - 执行待确认动作后生成工单，并在告警与工单页标记「Agent 自动」。
 */

test.describe('v3.28 设置页 Agent 自动执行', () => {
  test('页签可打开并与推送通道一起保存', async ({ page }) => {
    await login(page)
    await page.goto('/settings?tab=farm_agent')

    await expect(page.getByRole('tab', { name: 'Agent 自动执行' })).toBeVisible()
    await expect(page.getByText('启用自动执行')).toBeVisible()
    await expect(page.getByText('消息推送')).toBeVisible()
    await expect(page.getByRole('button', { name: '新增通道' })).toBeVisible()
    await expect(page.getByText('预警阈值')).toBeVisible()

    const card = page.locator('.section').filter({ hasText: 'Agent 自动执行' }).first()
    const toggle = card
      .locator('.el-form-item')
      .filter({ hasText: '启用自动执行' })
      .locator('.el-switch')

    // 打开再关回去：既验证保存链路，也不给后续规格留下启用态的定时任务
    await toggle.click()
    await card.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('自动执行设置已保存')).toBeVisible()
    await toggle.click()
    await card.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('自动执行设置已保存')).toBeVisible()
  })
})

async function apiToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/v1/auth/login', {
    data: { username: 'yard_admin', password: 'yard123' },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.access_token as string
}

/** 造一台只在本次出现的设备：降噪按设备 id 去重，新设备才能稳定产出新动作 */
async function seedDryDevice(request: APIRequestContext, token: string): Promise<void> {
  const name = `E2E墒情站${Date.now().toString().slice(-6)}`
  const created = await request.post('/api/v1/devices', {
    headers: { Authorization: `Bearer ${token}` },
    data: { name, device_type: 'weather', source_type: 'webhook' },
  })
  expect(created.ok()).toBeTruthy()
  const device = (await created.json()).data
  for (let i = 0; i < 12; i += 1) {
    const reported_at = new Date(Date.now() - (11 - i) * 3600_000).toISOString()
    const resp = await request.post(`/api/v1/devices/${device.id}/ingest`, {
      headers: { 'X-Device-Token': device.device_token },
      data: {
        reported_at,
        data: { soil_moisture_20: 30 - i, rain: 0, temp: 22, humidity: 55 },
      },
    })
    expect(resp.ok()).toBeTruthy()
  }
}

async function setLlmExplain(
  request: APIRequestContext,
  token: string,
  enabled: boolean,
): Promise<void> {
  const resp = await request.put('/api/v1/yards/current/settings/farm_agent', {
    headers: { Authorization: `Bearer ${token}` },
    data: { explain_with_llm: enabled },
  })
  expect(resp.ok()).toBeTruthy()
}

test.describe('v3.28 台账页 Agent 运行与动作执行', () => {
  test('立即运行产出动作，执行后生成带「Agent 自动」标记的工单', async ({ page, request }) => {
    const token = await apiToken(request)
    await seedDryDevice(request, token)
    // 关掉模型总结：动作全部由规则产生，测试不被上游时延拖住
    await setLlmExplain(request, token, false)

    try {
      await login(page)
      await page.goto('/farm')

      const panel = page.locator('.agent-panel')
      await expect(panel).toBeVisible()
      await expect(panel.getByText('Agent 自动执行')).toBeVisible()

      // 动作标题取「地块名」而非设备名（多设备可能落到同一地块），标题还会与
      // 历史动作重名；因此只断言本轮确实产出待确认动作，再执行最新一条验证工单链路。
      await panel.getByRole('button', { name: '立即运行' }).click()
      await expect(page.getByText(/已执行一轮：\d+ 条发现/)).toBeVisible({ timeout: 30_000 })

      const pending = panel.locator('.pending-item').first()
      await expect(pending).toBeVisible({ timeout: 15_000 })
      const title = (await pending.locator('strong').innerText()).trim()
      await pending.getByRole('button', { name: '执行' }).click()
      await expect(page.getByText('已执行该动作')).toBeVisible()

      await page.goto('/work-orders')
      const row = page.locator('.el-table__row').filter({ hasText: title }).first()
      await expect(row).toBeVisible()
      await expect(row.getByText('Agent 自动')).toBeVisible()
    } finally {
      await setLlmExplain(request, token, true)
    }
  })
})
