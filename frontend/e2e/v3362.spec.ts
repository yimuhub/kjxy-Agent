import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/**
 * 模型弹窗依赖已有供应商行；空库先造一条并立即停用，
 * 避免它被选为调用通道影响同一轮其它用例。
 */
async function seedSupplier(
  request: APIRequestContext,
): Promise<{ id: string; name: string }> {
  const resp = await request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  expect(resp.ok()).toBeTruthy()
  const token = (await resp.json()).data.access_token as string
  const name = `E2E供应商${Date.now().toString().slice(-6)}`
  const created = await request.post('/api/v1/llm/providers', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name,
      provider_type: 'openai',
      base_url: 'http://127.0.0.1:9/v1',
      model_id: 'e2e-chat',
      api_key: 'e2e-placeholder-key',
      available_models: ['e2e-chat'],
    },
  })
  expect(created.ok()).toBeTruthy()
  const supplier = (await created.json()).data
  await request.post(`/api/v1/llm/providers/${supplier.id}/disable`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  return { id: supplier.id, name }
}

async function removeSupplier(request: APIRequestContext, id: string): Promise<void> {
  const resp = await request.post('/api/v1/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  const token = (await resp.json()).data.access_token as string
  await request.delete(`/api/v1/llm/providers/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

test.describe('模型设置与表格列宽（v3.36.2）', () => {
  test('弹窗居中、模态自动识别、价格选填可保存、列宽可拖动', async ({ page, request }) => {
    const supplier = await seedSupplier(request)
    try {
      await login(page, 'admin', 'admin123')
      await page.goto('/model-center')
      await page.getByRole('tab', { name: '供应商', exact: true }).click()
      await page
        .getByRole('button', { name: /^模型$/ })
        .first()
        .click()

      const dialog = page.locator('.supplier-models-dialog')
      await expect(dialog).toBeVisible()
      const box = await dialog.boundingBox()
      const viewport = page.viewportSize()!
      expect(box).not.toBeNull()
      expect(Math.abs(box!.x + box!.width / 2 - viewport.width / 2)).toBeLessThan(40)
      expect(Math.abs(box!.y + box!.height / 2 - viewport.height / 2)).toBeLessThan(60)

      // 列宽拖拽
      const th = dialog.locator('th').filter({ hasText: '模型名 / 上游 ID' }).first()
      const before = (await th.boundingBox())!.width
      const handle = th.locator('.col-resize-handle')
      await expect(handle).toHaveCount(1)
      const hb = (await handle.boundingBox())!
      await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2)
      await page.mouse.down()
      await page.mouse.move(hb.x + hb.width / 2 + 120, hb.y + hb.height / 2, { steps: 8 })
      await page.mouse.up()
      const after = (await th.boundingBox())!.width
      expect(after).toBeGreaterThan(before + 60)

      // 新增模型：自动识别图像 + 价格留空保存
      await dialog.getByRole('button', { name: '新增模型' }).click()
      const edit = page.getByRole('dialog', { name: '新增模型' })
      await expect(edit).toBeVisible()
      const key = `ui-tmp-${Date.now()}`
      await edit.getByPlaceholder('客户端使用的逻辑 Key，如 deepseek-chat').fill(key)
      await edit.getByPlaceholder('留空则与模型 Key 相同').fill('gpt-image-1')
      await edit.getByText('展示名', { exact: true }).click()
      await expect(edit.getByText('已按模型名识别为「图像」')).toBeVisible({ timeout: 10_000 })
      await expect(edit.getByText('选填，留空表示不配置价格')).toBeVisible()
      await edit.getByRole('button', { name: '保存' }).click()
      await expect(edit).toBeHidden({ timeout: 10_000 })
      await expect(dialog.getByText(key, { exact: false }).first()).toBeVisible()

      // 清理测试模型
      const row = dialog.locator('tr').filter({ hasText: key })
      await row.getByRole('button', { name: '删除' }).click()
      await page.getByRole('button', { name: '确定' }).click()
      await expect(dialog.getByText(key, { exact: false })).toHaveCount(0, { timeout: 10_000 })
    } finally {
      await removeSupplier(request, supplier.id)
    }
  })
  test('编辑已有模型：价格留空也能保存（不再报 unit 必须是数字）', async ({ page, request }) => {
    const supplier = await seedSupplier(request)
    try {
      await login(page, 'admin', 'admin123')
      await page.goto('/model-center')
      await page.getByRole('tab', { name: '供应商', exact: true }).click()
      await page
        .getByRole('button', { name: /^模型$/ })
        .first()
        .click()

      const dialog = page.locator('.supplier-models-dialog')
      await expect(dialog).toBeVisible()
      await dialog.locator('tbody tr').first().getByRole('button', { name: '编辑' }).click()
      const edit = page.getByRole('dialog', { name: '编辑模型' })
      await expect(edit).toBeVisible()
      await expect(edit.getByText('选填，留空表示不配置价格')).toBeVisible()
      await edit.getByRole('button', { name: '保存' }).click()
      await expect(edit).toBeHidden({ timeout: 10_000 })
    } finally {
      await removeSupplier(request, supplier.id)
    }
  })
})
