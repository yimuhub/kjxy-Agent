import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.26 增长与服务技能包（D-1）：
 * 智能体模型集中配置、海报出图与签名取图、增长技能上架、合规/图像工具入库、
 * 以及演示经营线资料（技能引用的事实来源）。
 */

const AGENT_CODE = 'agriculture_qa'
const SUPPLIER_PREFIX = 'e2e326-'

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

async function createSupplier(
  request: APIRequestContext,
  token: string,
  name: string,
): Promise<string> {
  const resp = await request.post('/api/v1/llm/providers', {
    headers: auth(token),
    data: {
      name,
      provider_type: 'openai_compatible',
      base_url: 'https://e2e326.invalid/v1',
      model_id: 'e2e326-model',
      api_key: 'sk-e2e326-not-a-real-key',
    },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.id as string
}

async function purgeStaleSuppliers(request: APIRequestContext, token: string): Promise<void> {
  const resp = await request.get('/api/v1/llm/providers', { headers: auth(token) })
  expect(resp.ok()).toBeTruthy()
  const rows = (await resp.json()).data as { id: string; name: string }[]
  for (const item of rows) {
    if (item.name.startsWith(SUPPLIER_PREFIX)) {
      await request.delete(`/api/v1/llm/providers/${item.id}`, { headers: auth(token) })
    }
  }
}

async function putAgentBinding(
  request: APIRequestContext,
  token: string,
  supplierId: string,
): Promise<void> {
  const resp = await request.put('/api/v1/llm/bindings', {
    headers: auth(token),
    data: {
      scope_type: 'agent',
      scope_key: AGENT_CODE,
      entries: [{ supplier_id: supplierId }],
    },
  })
  expect(resp.ok()).toBeTruthy()
}

async function clearAgentBinding(request: APIRequestContext, token: string): Promise<void> {
  const resp = await request.put('/api/v1/llm/bindings', {
    headers: auth(token),
    data: { scope_type: 'agent', scope_key: AGENT_CODE, entries: [] },
  })
  expect(resp.ok()).toBeTruthy()
}

async function openAgentModels(page: Page) {
  await page.goto('/model-center')
  await expect(page.locator('.page-title')).toHaveText('模型中心', { timeout: 20_000 })
  await page.getByRole('tab', { name: '智能体模型' }).click()
  const pane = page.locator('.el-tab-pane:visible')
  await expect(pane.getByText('一个地方配完：跟随中心')).toBeVisible({ timeout: 20_000 })
  return pane
}

test.describe('增长与服务技能包（v3.26）', () => {
  test.describe.configure({ timeout: 180_000 })

  test('智能体模型：平台助手与视觉能力集中展示，视觉卡片可打开配置', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    const pane = await openAgentModels(page)

    await expect(pane.getByText(/智能体模型（\d+ \/ \d+）/)).toBeVisible()
    const row = pane.locator('.el-table tbody tr').filter({ hasText: AGENT_CODE }).first()
    await expect(row).toBeVisible()
    await expect(row.getByText('平台助手')).toBeVisible()
    await expect(row.getByText('跟随中心')).toBeVisible()
    // 没有单独配置时展示继承结果或“暂无可用供应商”，但不允许空白
    await expect(row.locator('td').nth(3)).not.toBeEmpty()

    await expect(pane.getByText('视觉能力默认模型（2）')).toBeVisible()
    const cards = pane.locator('.visual-card')
    await expect(cards).toHaveCount(2)
    await expect(cards.nth(0)).toContainText('图像生成')
    await expect(cards.nth(0)).toContainText('还没有可用的图像供应商')
    await expect(cards.nth(1)).toContainText('视频生成')
    await expect(cards.nth(1)).toContainText('视频生成通道已预留')

    await cards.nth(0).getByRole('button', { name: '配置' }).click()
    const dialog = page.getByRole('dialog').filter({ hasText: '绑定模型：图像生成' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('当前解析（这次会用谁）')).toBeVisible()
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(dialog).toBeHidden()
  })

  test('智能体模型：指定模型后显示指定模型，回到跟随中心可撤销', async ({ page, request }) => {
    const token = await apiToken(request)
    await purgeStaleSuppliers(request, token)
    await clearAgentBinding(request, token)

    const name = `${SUPPLIER_PREFIX}main-${Date.now().toString(36).slice(-6)}`
    const supplierId = await createSupplier(request, token, name)

    try {
      await putAgentBinding(request, token, supplierId)

      await login(page, 'admin', 'admin123')
      const pane = await openAgentModels(page)
      const row = pane.locator('.el-table tbody tr').filter({ hasText: AGENT_CODE }).first()
      await expect(row.getByText('指定模型')).toBeVisible()
      await expect(row).toContainText(name)

      // 调整弹窗按“用户能看懂的名字”展示，而不是裸编码
      await row.getByRole('button', { name: '调整' }).click()
      const dialog = page.getByRole('dialog').filter({ hasText: '绑定模型：' })
      await expect(dialog).toBeVisible()
      await expect(dialog).toContainText(AGENT_CODE)
      await dialog.getByRole('button', { name: '关闭', exact: true }).click()
      await expect(dialog).toBeHidden()

      await row.getByRole('button', { name: '回到跟随' }).click()
      const confirm = page.getByRole('dialog', { name: '回到跟随中心' })
      await expect(confirm).toContainText('清除「')
      await confirm.getByRole('button', { name: '确定' }).click()
      await expect(page.getByText('已回到跟随中心')).toBeVisible()
      await expect(row.getByText('跟随中心')).toBeVisible()
    } finally {
      await clearAgentBinding(request, token)
      await request.delete(`/api/v1/llm/providers/${supplierId}`, { headers: auth(token) })
    }
  })

  test('助手编辑页与模型中心用同一套术语：跟随中心 / 专用模型', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/agents/custom')
    await page.getByRole('button', { name: '+ 新建智能体' }).click()

    const dialog = page.getByRole('dialog', { name: '新建智能体' })
    await expect(dialog).toBeVisible()
    const modelField = dialog.locator('.el-form-item').filter({ hasText: '专用模型' })
    await expect(modelField).toBeVisible()
    await expect(modelField.getByText('跟随中心（能力/全局默认）')).toBeVisible()
    await expect(modelField.getByText('也可到「模型中心 → 智能体模型」统一配置主用与兜底')).toBeVisible()
    await dialog.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toBeHidden()
  })

  test('海报出图：无图像供应商时降级模板，签名可读、改签名即 404', async ({ request }) => {
    const token = await apiToken(request)
    const resp = await request.post('/api/v1/growth/posters', {
      headers: auth(token),
      data: {
        prompt: '丰收季苹果礼盒海报，暖色调，强调产地直发',
        title: '丰收季 · 苹果礼盒',
        subtitle: '产地直发 · 现摘现发',
        footer: '演示科技小院',
        theme: 'orchard',
        size: '1024x1024',
      },
    })
    expect(resp.ok()).toBeTruthy()
    const data = (await resp.json()).data as {
      ok: boolean
      fallback: boolean
      notice: string
      size: string
      assets: { key: string; url: string; mime: string; bytes: number }[]
      latency_ms: number
    }
    expect(data.ok).toBe(true)
    expect(data.size).toBe('1024x1024')
    expect(data.assets.length).toBeGreaterThan(0)
    expect(data.assets[0].url).toMatch(/^\/api\/v1\/growth\/posters\/.+\?sig=[0-9a-f]{32}$/)
    // 没有图像供应商时必须如实说明降级，不能假装是 AI 出图
    if (data.fallback) {
      expect(data.notice).toContain('模板')
      expect(data.assets[0].mime).toBe('image/svg+xml')
    }

    // 取图不依赖登录态（<img> 带不了 Authorization），但必须持签名
    const image = await request.get(data.assets[0].url)
    expect(image.status()).toBe(200)
    expect(image.headers()['content-type']).toContain('image/')
    expect((await image.body()).length).toBeGreaterThan(0)

    const forged = data.assets[0].url.replace(/sig=[0-9a-f]+$/, 'sig=deadbeefdeadbeefdeadbeefdeadbeef')
    const denied = await request.get(forged)
    expect(denied.status()).toBe(404)

    const badSize = await request.post('/api/v1/growth/posters', {
      headers: auth(token),
      data: { prompt: '尺寸校验用海报', size: '999x999' },
    })
    expect(badSize.status()).toBe(400)
    expect((await badSize.json()).error.code).toBe('INVALID_REQUEST')
  })

  test('增长技能：12 项内置技能可检索，海报两项在库中可见', async ({ page, request }) => {
    const token = await apiToken(request)
    const search = await request.post('/api/v1/market/items/search', {
      headers: auth(token),
      data: { source: 'builtin', item_type: 'skill', limit: 50 },
    })
    expect(search.ok()).toBeTruthy()
    const body = await search.json()
    const codes = (body.data.items as { code: string; item_key: string }[]).map(i => i.code)
    const growthCodes = codes.filter(code => code.startsWith('growth_'))
    expect(growthCodes).toHaveLength(12)
    expect(growthCodes).toEqual(
      expect.arrayContaining(['growth_poster_prompt', 'growth_poster_render', 'growth_event_plan']),
    )
    expect(body.data.total).toBe(24)

    await login(page, 'admin', 'admin123')
    await page.goto('/library')
    await expect(page.getByRole('heading', { name: '插件与技能库' })).toBeVisible()
    await expect(page.locator('.lib-card').first()).toBeVisible({ timeout: 20_000 })

    const keyword = page.getByLabel('搜索插件与技能')
    await keyword.fill('海报')
    await keyword.press('Enter')
    const hits = page.locator('.lib-card')
    await expect(hits).toHaveCount(2, { timeout: 20_000 })
    await expect(hits.nth(0)).toContainText(/海报/)
    await expect(hits.nth(1)).toContainText(/海报/)
  })

  test('可用工具：合规体检与海报出图已入目录，详情说明能力边界', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=tools')

    const pane = page.getByRole('tabpanel', { name: '可用工具' })
    await expect(pane).toContainText(/可用工具（\d+）/, { timeout: 20_000 })

    const detail = page.locator('.el-dialog:visible').filter({ has: page.locator('.detail-tags') })

    const complianceRow = pane
      .locator('.el-table tbody tr')
      .filter({ hasText: 'compliance.check_copy' })
      .first()
    await expect(complianceRow).toBeVisible()
    await expect(complianceRow.getByText('只读')).toBeVisible()
    await complianceRow.locator('.tool-link').click()
    await expect(detail).toBeVisible()
    await expect(detail.locator('.el-dialog__title')).toContainText('广告合规体检')
    await expect(detail.getByText('能做什么')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(detail).toBeHidden()

    const posterRow = pane
      .locator('.el-table tbody tr')
      .filter({ hasText: 'image.generate' })
      .first()
    await expect(posterRow).toBeVisible()
    await posterRow.locator('.tool-link').click()
    await expect(detail).toBeVisible()
    await expect(detail.locator('.el-dialog__title')).toContainText('图像生成（海报出图）')
    await page.keyboard.press('Escape')
    await expect(detail).toBeHidden()
  })

  test('演示经营线：产品与产销档案已入库，可作为技能事实来源', async ({ page, request }) => {
    const token = await apiToken(request)
    const resp = await request.get('/api/v1/knowledge/documents?page=1&page_size=50', {
      headers: auth(token),
    })
    expect(resp.ok()).toBeTruthy()
    const body = await resp.json()
    const docs = body.data as { title: string; chunk_count: number; status: string }[]
    const growthDoc = docs.find(doc => doc.title === '示范小院产品与产销档案（演示资料）')
    expect(growthDoc, '演示经营线资料应由 seed 幂等写入').toBeTruthy()
    expect(growthDoc!.chunk_count).toBeGreaterThan(0)
    expect(growthDoc!.status).toBe('ready')

    await login(page, 'admin', 'admin123')
    await page.goto('/knowledge')
    await expect(page.getByText('示范小院产品与产销档案（演示资料）')).toBeVisible({
      timeout: 20_000,
    })
  })
})
