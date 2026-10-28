import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/** v3.22 一句话业务：预览 → 确认 → 列表可见；缺字段时引导回原表单。 */

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

async function createPlot(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<void> {
  const resp = await request.post('/api/v1/farm/plots', {
    headers: auth(token),
    data: { name, crop: '水稻', area_mu: 6 },
  })
  expect(resp.status()).toBe(201)
}

async function createAlert(
  request: APIRequestContext,
  token: string,
  title: string,
  plotId: string,
): Promise<void> {
  const resp = await request.post('/api/v1/farm/alerts', {
    headers: auth(token),
    data: {
      title,
      detail: '虫情计数连续 3 天上升',
      severity: 'high',
      plot_id: plotId,
    },
  })
  expect(resp.status()).toBe(201)
}

function unique(prefix: string): string {
  return `${prefix}${Date.now().toString().slice(-6)}`
}

test.describe('一句话业务（v3.22）', () => {
  test('台账：一句话新增地块，再说一句记农事', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/farm')

    const name = unique('E2E田')
    const input = page.getByPlaceholder(/说一句：新增地块/)
    await expect(input).toBeVisible()

    await input.fill(`新增地块 ${name} 水稻 12 亩，沙壤土`)
    await page.getByRole('button', { name: '解析' }).click()
    const dialog = page.getByRole('dialog', { name: '确认办理' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText(name)
    await expect(dialog).toContainText('12')
    await dialog.getByRole('button', { name: '确认办理' }).click()
    await expect(page.getByText(`已新增地块「${name}」`)).toBeVisible()

    await page.getByRole('tab', { name: '地块与周期' }).click()
    await expect(page.getByText(name).first()).toBeVisible()

    await input.fill(`${name} 今天追肥 尿素 5 公斤`)
    await page.getByRole('button', { name: '解析' }).click()
    await expect(dialog).toContainText('施肥')
    await expect(dialog).toContainText('5公斤')
    await dialog.getByRole('button', { name: '确认办理' }).click()
    await expect(page.getByText(new RegExp(`已在「${name}」记录施肥`))).toBeVisible()
    await expect(page.getByText(name).first()).toBeVisible()
  })

  test('诊断：一句话登记症状并落库', async ({ page, request }) => {
    const token = await apiToken(request)
    const name = unique('E2E诊断田')
    await createPlot(request, token, name)

    await login(page, 'admin', 'admin123')
    await page.goto('/diagnosis')

    await page
      .getByPlaceholder(/说一句：东一号田/)
      .fill(`${name} 水稻 叶片发黄有褐斑，最近连阴雨`)
    await page.getByRole('button', { name: '解析' }).click()
    const dialog = page.getByRole('dialog', { name: '确认办理' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('水稻')
    await expect(dialog).toContainText('叶片发黄')
    await dialog.getByRole('button', { name: '确认办理' }).click()
    await expect(page.getByText('已为「水稻」登记诊断单')).toBeVisible()
    await expect(page.getByText(name).first()).toBeVisible()
  })

  test('工单：一句话把告警转成工单', async ({ page, request }) => {
    const token = await apiToken(request)
    const name = unique('E2E告警田')
    await createPlot(request, token, name)
    const plotId = await request
      .get('/api/v1/farm/plots', { headers: auth(token) })
      .then(async resp => {
        const rows = (await resp.json()).data as Array<{ plot_id: string; name: string }>
        return rows.find(row => row.name === name)?.plot_id ?? ''
      })
    expect(plotId).not.toBe('')
    await createAlert(request, token, `${name}虫情告警`, plotId)

    await login(page, 'admin', 'admin123')
    await page.goto('/work-orders')

    await page.getByPlaceholder(/说一句：把东一号田/).fill(`把${name}的虫情告警转成工单，明天到期`)
    await page.getByRole('button', { name: '解析' }).click()
    const dialog = page.getByRole('dialog', { name: '确认办理' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('把告警')
    await expect(dialog).toContainText(name)
    await expect(dialog).toContainText('到期')
    await dialog.getByRole('button', { name: '确认办理' }).click()
    await expect(page.getByText(/已把告警转成工单/)).toBeVisible()
    await expect(page.getByText(`${name}虫情告警`).first()).toBeVisible()
  })

  test('缺字段：明确提示并可用原表单继续', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/farm')

    // 预览接口按契约返回缺字段（避免依赖模型补全的随机性）
    await page.route('**/api/v1/quick-actions/preview', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: {
            module: 'farm',
            action: 'plot.create',
            payload: {
              name: 'E2E缺面积田',
              crop: '水稻',
              area_mu: null,
              soil_type: '',
              notes: '',
            },
            summary: '地块「E2E缺面积田」，作物 水稻',
            missing: ['面积（如 12 亩）'],
            ready: false,
            source: 'rule',
            fingerprint: 'a'.repeat(64),
          },
        }),
      }),
    )

    await page.getByPlaceholder(/说一句：新增地块/).fill('新增地块 E2E缺面积田 水稻')
    await page.getByRole('button', { name: '解析' }).click()
    const preview = page.getByRole('dialog', { name: '确认办理' })
    await expect(preview).toBeVisible()
    await expect(preview).toContainText('还缺：面积')
    await preview.getByRole('button', { name: '用表单填写' }).click()

    const plotDialog = page.getByRole('dialog', { name: '新增地块' })
    await expect(plotDialog).toBeVisible()
    await expect(plotDialog.locator('input').first()).toHaveValue('E2E缺面积田')
    await plotDialog.getByRole('button', { name: '取消' }).click()
  })
})
