import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.25 模型中心与可用工具目录：
 * 模型配置从系统设置迁到「小院管理 · 模型中心」，同层兜底顺序可编辑；
 * 工具点击名称看详情，支持手动填写与一句话生成草稿（草稿不自动对模型可见）。
 */

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
      base_url: 'https://e2e325.invalid/v1',
      model_id: 'e2e325-model',
      api_key: 'sk-e2e325-not-a-real-key',
    },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.id as string
}

async function clearPurposeBinding(
  request: APIRequestContext,
  token: string,
  scopeKey: string,
): Promise<void> {
  const resp = await request.put('/api/v1/llm/bindings', {
    headers: auth(token),
    data: { scope_type: 'purpose', scope_key: scopeKey, entries: [] },
  })
  expect(resp.ok()).toBeTruthy()
}

async function pickSupplier(page: Page, row: Locator, label: string): Promise<void> {
  // Element Plus 的浮层挂在 body 上且带收起动画：先确认没有残留的下拉，再点这一档，
  // 否则 Playwright 会命中正在淡出的上一层，报「element is not visible」。
  await expect(page.locator('.el-select-dropdown:visible')).toHaveCount(0, { timeout: 10_000 })
  await row.locator('.entry-supplier').click()
  const popper = page.locator('.el-select-dropdown:visible')
  await expect(popper).toHaveCount(1, { timeout: 10_000 })
  await popper.locator('.el-select-dropdown__item', { hasText: label }).click({ timeout: 15_000 })
}

/** 上一轮超时/中断会留下半截供应商，先扫干净再建，避免选项列表越滚越长。 */
async function purgeStaleSuppliers(request: APIRequestContext, token: string): Promise<void> {
  const resp = await request.get('/api/v1/llm/providers', { headers: auth(token) })
  expect(resp.ok()).toBeTruthy()
  const rows = (await resp.json()).data as { id: string; name: string }[]
  for (const item of rows) {
    if (item.name.startsWith('e2e325-')) {
      await request.delete(`/api/v1/llm/providers/${item.id}`, { headers: auth(token) })
    }
  }
}

/** 同理清掉上一轮遗留的 e2e325_ 工具，避免「编码重复」把用例卡死。 */
async function purgeStaleTools(request: APIRequestContext, token: string): Promise<void> {
  const resp = await request.get('/api/v1/tools/catalog', { headers: auth(token) })
  expect(resp.ok()).toBeTruthy()
  const items = ((await resp.json()).data as { items: { code: string }[] }).items
  for (const item of items) {
    if (item.code.startsWith('e2e325_')) {
      await request.delete(`/api/v1/tools/${item.code}`, { headers: auth(token) })
    }
  }
}

const PURPOSE_KEY = 'skill_suggest'

test.describe('模型中心与工具目录（v3.25）', () => {
  // 首次进入 /model-center 与 /settings 需要现编前端分块，留出冷启动余量
  test.describe.configure({ timeout: 180_000 })

  test('模型中心：入口、当前生效解析与测试当前链路', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/model-center')

    await expect(page.locator('.page-title')).toHaveText('模型中心', { timeout: 20_000 })
    await expect(page.getByText('当前生效解析')).toBeVisible()
    await expect(page.getByText('对话会用谁')).toBeVisible()
    await expect(page.getByText('出于安全考虑，此处绝不展示密钥明文')).toBeVisible()

    // 四个页签都在，且模型配置已不在系统设置里
    for (const tab of ['供应商', '全局默认与兜底', '按能力绑定', '按智能体绑定']) {
      await expect(page.getByRole('tab', { name: tab })).toBeVisible()
    }
    await page.goto('/settings')
    await expect(page.getByRole('tab', { name: '模型配置' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '模型供应商' })).toHaveCount(0)

    // 测试当前链路：无论通不通都要给出明确结论
    await page.goto('/model-center')
    await page.getByRole('button', { name: '测试当前链路' }).click()
    await expect(page.locator('.el-message').filter({ hasText: '链路' })).toBeVisible({ timeout: 75_000 })
  })

  test('按能力绑定：主用+兜底顺序可保存、回显并回落到全局默认', async ({ page, request }) => {
    const token = await apiToken(request)
    await purgeStaleSuppliers(request, token)
    await clearPurposeBinding(request, token, PURPOSE_KEY)
    const mainName = `e2e325-main-${Date.now().toString(36).slice(-6)}`
    const spareName = `e2e325-spare-${Date.now().toString(36).slice(-6)}`
    const mainId = await createSupplier(request, token, mainName)
    const spareId = await createSupplier(request, token, spareName)

    try {
      await login(page, 'admin', 'admin123')
      await page.goto('/model-center')
      await page.getByRole('tab', { name: '按能力绑定' }).click()

      const row = page.locator('.el-table tbody tr').filter({ hasText: '技能改进建议' }).first()
      await expect(row.getByText('跟随全局默认')).toBeVisible()
      await row.getByRole('button', { name: '配置' }).click()

      const dialog = page.getByRole('dialog').filter({ hasText: '绑定模型：' })
      await expect(dialog).toBeVisible()

      await dialog.getByRole('button', { name: '添加一档兜底' }).click()
      await dialog.getByRole('button', { name: '添加一档兜底' }).click()
      const rows = dialog.locator('.entry-row')
      await expect(rows).toHaveCount(2)
      await pickSupplier(page, rows.nth(0), mainName)
      await pickSupplier(page, rows.nth(1), spareName)
      await rows.nth(1).locator('.entry-model input').fill('e2e325-override')

      await dialog.getByRole('button', { name: '保存', exact: true }).click()
      await expect(page.getByText('模型绑定已保存并即时生效')).toBeVisible()

      // 列表回显：主用 → 兜底1，且顺序与界面一致
      await expect(row.locator('.bind-tag').nth(0)).toContainText(`主用 ${mainName}`)
      await expect(row.locator('.bind-tag').nth(1)).toContainText(`兜底1 ${spareName}`)
      await expect(row.locator('.bind-tag').nth(1)).toContainText('e2e325-override')

      // 同一层重复供应商被前端拦下，不会写出无意义的兜底链
      await dialog.getByRole('button', { name: '添加一档兜底' }).click()
      await pickSupplier(page, dialog.locator('.entry-row').nth(2), mainName)
      await dialog.getByRole('button', { name: '保存', exact: true }).click()
      await expect(page.getByText('同一层内供应商不能重复')).toBeVisible()
      await dialog.locator('.entry-row').nth(2).getByRole('button', { name: '移除' }).click()

      // 上移兜底档：交换主用与兜底顺序
      await dialog.locator('.entry-row').nth(1).getByRole('button', { name: '上移' }).click()
      await dialog.getByRole('button', { name: '保存', exact: true }).click()
      await expect(page.getByText('模型绑定已保存并即时生效')).toBeVisible()
      await expect(row.locator('.bind-tag').nth(0)).toContainText(`主用 ${spareName}`)

      // 解析预览按「这次会用谁」列出链路
      await expect(dialog.getByText('当前解析（这次会用谁）')).toBeVisible()
      await expect(dialog.locator('.resolution-hops li').first()).toContainText(spareName)

      await dialog.getByRole('button', { name: '关闭', exact: true }).click()
      await clearPurposeBinding(request, token, PURPOSE_KEY)
      await page.reload()
      await page.getByRole('tab', { name: '按能力绑定' }).click()
      await expect(
        page.locator('.el-table tbody tr').filter({ hasText: '技能改进建议' }).first().getByText('跟随全局默认'),
      ).toBeVisible()
    } finally {
      for (const id of [mainId, spareId]) {
        await request.delete(`/api/v1/llm/providers/${id}`, { headers: auth(token) })
      }
    }
  })

  test('可用工具：点名称看详情、手动添加草稿→启用→删除', async ({ page, request }) => {
    const token = await apiToken(request)
    await purgeStaleTools(request, token)
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=tools')

    // 计数文案被插值拆成多个文本节点，用 toContainText 断言整块面板
    const pane = page.getByRole('tabpanel', { name: '可用工具' })
    await expect(pane).toContainText(/可用工具（\d+）/, { timeout: 20_000 })
    await expect(pane).toContainText(/内置 \d+ · 本小院 \d+ · MCP \d+/)

    const first = page.locator('.tool-link').first()
    const firstName = (await first.innerText()).trim()
    await first.click()
    // 详情已从右侧抽屉改为居中弹窗（v3.25.1）：按 .detail-tags 定位当前可见的工具详情弹窗
    const detail = page.locator('.el-dialog:visible').filter({ has: page.locator('.detail-tags') })
    await expect(detail).toBeVisible()
    await expect(detail.getByText('能做什么')).toBeVisible()
    await expect(detail.getByText('调用示例')).toBeVisible()
    await expect(detail.getByText('执行方式')).toBeVisible()
    await expect(detail.locator('.el-dialog__title')).toContainText(firstName)
    await page.keyboard.press('Escape')
    await expect(detail).toBeHidden()

    // 手动添加 → 草稿（默认不启用，模型看不见）→ 启用 → 删除
    const code = `e2e325_tool_${Date.now().toString(36).slice(-6)}`
    const name = `E2E 325 工具 ${code}`
    await page.getByRole('button', { name: '添加可用工具' }).click()
    await page.locator('.el-dropdown-menu__item:visible')
      .filter({ hasText: '手动填写' })
      .click()

    const dialog = page.getByRole('dialog', { name: '手动添加工具' })
    await expect(dialog).toBeVisible()
    await dialog.locator('.el-form-item').filter({ hasText: '工具名称' }).locator('input').fill(name)
    await dialog.locator('.el-form-item').filter({ hasText: '工具编码' }).locator('input').fill(code)
    await dialog
      .locator('.el-form-item')
      .filter({ hasText: '提示词' })
      .locator('textarea')
      .fill('请用三句话总结 {crop} 的今日设备告警')
    await dialog
      .locator('.el-form-item')
      .filter({ hasText: '参数' })
      .locator('textarea')
      .fill('crop:string:作物名:必填')
    await dialog.getByRole('button', { name: '存为草稿' }).click()
    await expect(page.getByText('草稿已保存')).toBeVisible()

    const toolRow = page.locator('.el-table tbody tr').filter({ hasText: code }).first()
    await expect(toolRow).toBeVisible()
    await expect(toolRow.getByText('草稿/停用')).toBeVisible()
    await toolRow.getByRole('button', { name: '启用' }).click()
    await expect(page.getByText('已启用，下一次对话即可被调用')).toBeVisible()
    await expect(toolRow.getByText('已启用')).toBeVisible()

    // 详情里执行方式回显为复用小院模型推理，参数表带必填标记
    await toolRow.getByRole('button', { name: '详情' }).click()
    await expect(detail.getByText('复用小院已配置模型推理')).toBeVisible()
    await expect(detail.locator('.el-table tbody tr').filter({ hasText: 'crop' })).toContainText('必填')
    await page.keyboard.press('Escape')

    await toolRow.getByRole('button', { name: '删除' }).click()
    const confirm = page.getByRole('dialog', { name: '删除工具' })
    await confirm.getByRole('button', { name: '删除' }).click()
    await expect(page.getByText('已删除')).toBeVisible()
    await expect(page.locator('.el-table tbody tr').filter({ hasText: code })).toHaveCount(0)
  })

  test('一句话添加：模型不可用时给保守草稿，人工确认后才启用', async ({ page, request }) => {
    const token = await apiToken(request)
    const code = `e2e325_ai_${Date.now().toString(36).slice(-6)}`
    try {
      await purgeStaleTools(request, token)
      await login(page, 'admin', 'admin123')
      await page.goto('/settings?tab=tools')

      await page.getByRole('button', { name: '添加可用工具' }).click()
      await page
        .locator('.el-dropdown-menu__item:visible')
        .filter({ hasText: '一句话添加' })
        .click()

      const dialog = page.getByRole('dialog', { name: '一句话添加可用工具' })
      await expect(dialog).toBeVisible()
      await dialog.locator('textarea').first().fill('把今天的设备告警总结成三句话')
      await dialog.getByRole('button', { name: '生成草稿' }).click()

      // 两条分支都要给出明确出处，绝不允许静默落库
      const alert = dialog.locator('.el-alert')
      await expect(alert).toBeVisible({ timeout: 80_000 })
      await expect(dialog.getByText(/模型已产出草稿|模型输出不可用，已给出保守草稿/)).toBeVisible()
      await expect(dialog.getByRole('button', { name: '存为草稿' })).toBeEnabled()

      await dialog.locator('.el-form-item').filter({ hasText: '工具编码' }).locator('input').fill(code)
      await dialog.locator('.el-form-item').filter({ hasText: '工具名称' }).locator('input').fill('E2E 一句话草稿')
      await dialog.getByRole('button', { name: '存为草稿' }).click()
      await expect(page.getByText('草稿已保存，确认后点「启用」')).toBeVisible()

      const aiRow = page.locator('.el-table tbody tr').filter({ hasText: code }).first()
      await expect(aiRow).toBeVisible()
      await expect(aiRow.getByText('草稿/停用')).toBeVisible()
      await aiRow.getByRole('button', { name: '启用' }).click()
      await expect(aiRow.getByText('已启用')).toBeVisible()
    } finally {
      await request.delete(`/api/v1/tools/${code}`, { headers: auth(token) })
    }
  })

  test('无 llm:manage 权限的成员看不到模型中心入口', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('operator')
    await page.locator('input[autocomplete="current-password"]').fill('operator123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page).toHaveURL(/\/(chat)?$/)

    await expect(page.locator('#main-nav').getByText('模型中心')).toHaveCount(0)
    await page.goto('/model-center')
    await expect(page.getByText('没有访问权限')).toBeVisible()
  })
})
