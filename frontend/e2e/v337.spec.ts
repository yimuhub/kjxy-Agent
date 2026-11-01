import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.37 模型选择全面改为「供应商 → 模型」级联下拉：
 * 视觉能力卡片、按能力绑定、智能体主用模型都能直接下拉修改，且只列模态匹配的模型；
 * 单个助手还能单独指定图像/视频模型（专属能力）。
 */

const AGENT_CODE = 'agriculture_qa'
const SUPPLIER_PREFIX = 'e2e337-'
const IMAGE_MODEL = 'e2e337-image'
const VIDEO_MODEL = 'e2e337-video'
const TEXT_MODEL = 'e2e337-text'

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
      base_url: 'https://e2e337.invalid/v1',
      model_id: TEXT_MODEL,
      api_key: 'sk-e2e337-not-a-real-key',
    },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.id as string
}

async function addModel(
  request: APIRequestContext,
  token: string,
  supplierId: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const resp = await request.post(`/api/v1/llm/providers/${supplierId}/models`, {
    headers: auth(token),
    data: payload,
  })
  expect(resp.ok()).toBeTruthy()
}

async function clearLayer(
  request: APIRequestContext,
  token: string,
  scopeType: 'purpose' | 'agent' | 'agent_purpose',
  scopeKey: string,
): Promise<void> {
  const resp = await request.put('/api/v1/llm/bindings', {
    headers: auth(token),
    data: { scope_type: scopeType, scope_key: scopeKey, entries: [] },
  })
  expect(resp.ok()).toBeTruthy()
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

/** 点开一个级联下拉并按供应商分组选模型（选项文案里带供应商名与模型名） */
async function pickModel(page: Page, scope: Locator, optionText: string): Promise<void> {
  await expect(page.locator('.el-select-dropdown:visible')).toHaveCount(0, { timeout: 10_000 })
  await scope.locator('.supplier-model-select').click()
  const popper = page.locator('.el-select-dropdown:visible')
  await expect(popper).toHaveCount(1, { timeout: 10_000 })
  await popper
    .locator('.el-select-dropdown__item', { hasText: optionText })
    .first()
    .click({ timeout: 15_000 })
}

test.describe('模型选择供应商→模型级联（v3.37）', () => {
  test.describe.configure({ timeout: 240_000 })

  test('视觉卡片直接下拉：只列图像/视频模型，选完即生效并可清除', async ({ page, request }) => {
    const token = await apiToken(request)
    await purgeStaleSuppliers(request, token)
    await clearLayer(request, token, 'purpose', 'image_generation')
    await clearLayer(request, token, 'purpose', 'video_generation')

    const name = `${SUPPLIER_PREFIX}${Date.now().toString(36).slice(-6)}`
    const supplierId = await createSupplier(request, token, name)
    await addModel(request, token, supplierId, {
      model_key: IMAGE_MODEL,
      upstream_model_id: IMAGE_MODEL,
      display_name: IMAGE_MODEL,
      modality: 'image',
      protocol: 'openai_images',
    })
    await addModel(request, token, supplierId, {
      model_key: VIDEO_MODEL,
      upstream_model_id: VIDEO_MODEL,
      display_name: VIDEO_MODEL,
      modality: 'video',
      protocol: 'openai_video',
    })

    try {
      await login(page, 'admin', 'admin123')
      await page.goto('/model-center')
      await page.getByRole('tab', { name: '智能体模型' }).click()
      const pane = page.locator('.el-tab-pane:visible')
      await expect(pane.getByText('视觉能力默认模型（2）')).toBeVisible({ timeout: 20_000 })

      const imageCard = pane.locator('.visual-card').nth(0)
      await expect(imageCard).toContainText('图像生成')
      // 拉出下拉：图像模型可选，对话模型不得出现（按模态过滤）
      await imageCard.locator('.supplier-model-select').click()
      const imagePopper = page.locator('.el-select-dropdown:visible')
      await expect(imagePopper).toHaveCount(1, { timeout: 10_000 })
      await expect(imagePopper.locator('.el-select-dropdown__item', { hasText: IMAGE_MODEL }).first()).toBeVisible()
      await expect(imagePopper.locator('.el-select-dropdown__item', { hasText: TEXT_MODEL })).toHaveCount(0)
      await imagePopper.locator('.el-select-dropdown__item', { hasText: IMAGE_MODEL }).first().click()
      await expect(page.getByText('图像生成默认模型已更新')).toBeVisible()
      await expect(imageCard).toContainText('已指定')
      await expect(imageCard).toContainText(IMAGE_MODEL)

      const videoCard = pane.locator('.visual-card').nth(1)
      await pickModel(page, videoCard, VIDEO_MODEL)
      await expect(page.getByText('视频生成默认模型已更新')).toBeVisible()
      await expect(videoCard).toContainText('已指定')

      await imageCard.getByRole('button', { name: '清除', exact: true }).click()
      await expect(page.getByText('已清除图像生成默认模型')).toBeVisible()
      await expect(imageCard).toContainText('未指定')
      await videoCard.getByRole('button', { name: '清除', exact: true }).click()
      await expect(page.getByText('已清除视频生成默认模型')).toBeVisible()
    } finally {
      await clearLayer(request, token, 'purpose', 'image_generation')
      await clearLayer(request, token, 'purpose', 'video_generation')
      await request.delete(`/api/v1/llm/providers/${supplierId}`, { headers: auth(token) })
    }
  })

  test('按能力绑定与智能体主用都能下拉即改，专属能力只影响该助手', async ({ page, request }) => {
    const token = await apiToken(request)
    await purgeStaleSuppliers(request, token)
    await clearLayer(request, token, 'purpose', 'skill_suggest')
    await clearLayer(request, token, 'agent', AGENT_CODE)
    await clearLayer(request, token, 'agent_purpose', `${AGENT_CODE}::image_generation`)

    const name = `${SUPPLIER_PREFIX}${Date.now().toString(36).slice(-6)}`
    const supplierId = await createSupplier(request, token, name)
    await addModel(request, token, supplierId, {
      model_key: IMAGE_MODEL,
      upstream_model_id: IMAGE_MODEL,
      display_name: IMAGE_MODEL,
      modality: 'image',
      protocol: 'openai_images',
    })

    try {
      await login(page, 'admin', 'admin123')
      await page.goto('/model-center')

      // 1) 按能力绑定：表格里直接下拉换主用模型
      await page.getByRole('tab', { name: '按能力绑定' }).click()
      const capRow = page.locator('.el-table tbody tr').filter({ hasText: '技能改进建议' }).first()
      await expect(capRow).toBeVisible()
      await pickModel(page, capRow, `${name} · ${TEXT_MODEL}`)
      await expect(page.getByText('已更新主用模型，下一次调用即生效')).toBeVisible()
      await expect(capRow.locator('.supplier-model-select')).toContainText(name)

      // 2) 智能体模型：主用列直接下拉换模型
      await page.getByRole('tab', { name: '智能体模型' }).click()
      const pane = page.locator('.el-tab-pane:visible')
      const agentRow = pane.locator('.el-table tbody tr').filter({ hasText: AGENT_CODE }).first()
      await expect(agentRow).toBeVisible({ timeout: 20_000 })
      await pickModel(page, agentRow, `${name} · ${TEXT_MODEL}`)
      await expect(page.getByText('的主用模型已更新')).toBeVisible()
      await expect(agentRow.getByText('指定模型')).toBeVisible()

      // 3) 专属能力：只给这个助手绑图像模型，不改变视觉默认
      await agentRow.getByRole('button', { name: '专属能力', exact: true }).click()
      const capDialog = page.getByRole('dialog').filter({ hasText: '专属能力：' })
      await expect(capDialog).toBeVisible()
      const imageCap = capDialog.locator('.cap-row').filter({ hasText: '图像生成' }).first()
      await pickModel(page, imageCap, `${name} · ${IMAGE_MODEL}`)
      await expect(page.getByText('专属能力模型已更新')).toBeVisible()
      await expect(imageCap.getByText('已指定')).toBeVisible()
      await capDialog.getByRole('button', { name: '关闭', exact: true }).click()
      await expect(capDialog).toBeHidden()
      await expect(agentRow).toContainText(`图像生成：${name}`)

      // 清除后回落到视觉默认
      await agentRow.getByRole('button', { name: '专属能力', exact: true }).click()
      await expect(capDialog).toBeVisible()
      await capDialog.getByRole('button', { name: '清除', exact: true }).first().click()
      await expect(page.getByText('已清除专属能力模型，回落到视觉默认模型')).toBeVisible()
      await capDialog.getByRole('button', { name: '关闭', exact: true }).click()
    } finally {
      await clearLayer(request, token, 'purpose', 'skill_suggest')
      await clearLayer(request, token, 'agent', AGENT_CODE)
      await clearLayer(request, token, 'agent_purpose', `${AGENT_CODE}::image_generation`)
      await request.delete(`/api/v1/llm/providers/${supplierId}`, { headers: auth(token) })
    }
  })
})
