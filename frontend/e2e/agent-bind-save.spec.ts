import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/**
 * 智能体「编辑 → 绑定技能 → 保存」死路回归（v3.78）。
 *
 * 用户实测：编辑助手时多绑几个技能就「没办法确认」。根因是读写口径不对称——
 * 运行期容忍历史裸编码，写入侧只认 skill:/plugin: 前缀，一保存就 422。
 * 修复后写入侧归一化 + 失效绑定提供「移除」逃生口。这里用真实浏览器守住两条路：
 *   A. 全选大量技能后保存成功（弹窗关闭、后端确实落库 N 条），不再卡死；
 *   B. 已绑定但不在候选集的项显示为「已失效」且可逐项移除，移除后能保存。
 *
 * 每条用例自建专属智能体（唯一编码），结束软删除收尾，不污染演示数据。
 * B 的「失效」通过拦下候选接口、隐藏一个已绑技能来模拟（等价于已下架/无权限），
 * 不依赖具体数据库；后端 422 + unknown_codes 契约另由集成测试覆盖。
 */

const YARD_ADMIN = { username: 'yard_admin', password: 'yard123' }

async function authToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/v1/auth/login', { data: YARD_ADMIN })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.access_token as string
}

interface BindableSkill {
  code: string
  name: string
}

async function fetchSkills(request: APIRequestContext, token: string): Promise<BindableSkill[]> {
  const resp = await request.get('/api/v1/agents/bindable-extensions', {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.skills as BindableSkill[]
}

async function createAgent(
  request: APIRequestContext,
  token: string,
  boundSkills: string[],
): Promise<{ id: string; code: string }> {
  const code = `zz_e2e_bind_${Date.now().toString().slice(-6)}${Math.floor(Math.random() * 1000)}`
  const resp = await request.post('/api/v1/agents/declarative', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      agent_code: code,
      name: `E2E绑定回归${code.slice(-6)}`,
      persona_prompt: '你是测试助手。',
      task_prompt_template: '请回答：{{question}}',
      allowed_tools: ['knowledge.search'],
      knowledge_scopes: ['yard'],
      visibility: 'public',
      bound_skills: boundSkills,
    },
  })
  expect(resp.ok()).toBeTruthy()
  return { id: (await resp.json()).data.id as string, code }
}

async function deleteAgent(request: APIRequestContext, token: string, id: string): Promise<void> {
  await request.delete(`/api/v1/agents/declarative/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

/** 强制列表视图并按唯一编码定位到专属智能体行，打开编辑弹窗 */
async function openEditByCode(page: import('@playwright/test').Page, code: string) {
  await login(page)
  await page.evaluate(() => localStorage.setItem('ky_custom_agents_view', 'list'))
  await page.goto('/agents/custom')
  await page.getByPlaceholder('搜索智能体名称').fill(code)
  const row = page.locator('.el-table__row').filter({ hasText: code })
  await expect(row).toHaveCount(1)
  await row.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: '编辑', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: /编辑智能体/ })
  await expect(dialog).toBeVisible()
  return dialog
}

test.describe('智能体绑定技能保存死路回归（v3.78）', () => {
  test('全选大量技能后保存成功，弹窗关闭且后端落库全部绑定', async ({ page, request }) => {
    const token = await authToken(request)
    const skills = await fetchSkills(request, token)
    // 折叠态只渲染前 3 行，候选必须多于 3 才有「展开全部」可点
    expect(skills.length).toBeGreaterThanOrEqual(5)
    const agent = await createAgent(request, token, [])
    try {
      const dialog = await openEditByCode(page, agent.code)
      const skillItem = dialog.locator('.el-form-item').filter({ hasText: '扩展技能' })

      // 展开完整候选弹层 → 全选
      await skillItem.getByRole('button', { name: /展开全部/ }).click()
      const popover = page.locator('.ext-popover')
      await expect(popover).toBeVisible()
      await skillItem.getByRole('button', { name: '全选', exact: true }).click()
      await expect(skillItem.locator('.ext-count')).toHaveText(`已选 ${skills.length}`)
      await expect(popover.locator('.el-checkbox.is-checked')).toHaveCount(skills.length)

      // 点弹层外（弹窗标题）收起弹层：弹层关掉、外层弹窗不被连带关闭（孤儿弹层修复）
      await dialog.locator('.el-dialog__title').click()
      await expect(popover).toBeHidden()
      await expect(dialog).toBeVisible()

      // 保存：后端确实写入 N 条绑定，弹窗关闭 = 不再「没办法确认」
      const saved = page.waitForResponse(
        res =>
          res.url().includes(`/agents/declarative/${agent.id}`) &&
          res.request().method() === 'PATCH',
      )
      await dialog.getByRole('button', { name: '保存', exact: true }).click()
      const resp = await saved
      expect(resp.status()).toBe(200)
      expect((await resp.json()).data.bound_skills).toHaveLength(skills.length)
      await expect(page.locator('.el-message').filter({ hasText: '智能体配置已保存' })).toBeVisible()
      await expect(dialog).toHaveCount(0)
    } finally {
      await deleteAgent(request, token, agent.id)
    }
  })

  test('失效绑定可见可移除，移除后保存成功（逃生口）', async ({ page, request }) => {
    const token = await authToken(request)
    const skills = await fetchSkills(request, token)
    expect(skills.length).toBeGreaterThanOrEqual(2)
    const hidden = skills[0].code
    const kept = skills[1].code
    const agent = await createAgent(request, token, [hidden, kept])
    try {
      // 模拟「已下架/无权限」：候选接口里隐藏一个已绑技能 → 前端把它标为已失效
      await page.route('**/api/v1/agents/bindable-extensions', async route => {
        const upstream = await route.fetch()
        const json = await upstream.json()
        if (json?.data?.skills) {
          json.data.skills = json.data.skills.filter((s: BindableSkill) => s.code !== hidden)
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(json),
        })
      })

      const dialog = await openEditByCode(page, agent.code)
      const skillItem = dialog.locator('.el-form-item').filter({ hasText: '扩展技能' })

      // 失效项显式列出（不再「看不见的绑定」），带移除按钮，计数仍含两条
      const staleRow = skillItem.locator('.ext-stale-row')
      await expect(staleRow).toHaveCount(1)
      await expect(staleRow.locator('.ext-stale-name')).toContainText(hidden.replace(/^skill:/, ''))
      await expect(staleRow.getByRole('button', { name: '移除' })).toBeVisible()
      await expect(skillItem.locator('.ext-count')).toHaveText('已选 2')

      // 逐项移除失效绑定 → 计数下降，逃生口生效
      await staleRow.getByRole('button', { name: '移除' }).click()
      await expect(skillItem.locator('.ext-stale-row')).toHaveCount(0)
      await expect(skillItem.locator('.ext-count')).toHaveText('已选 1')

      // 移除后保存成功：后端只剩保留的那条合法绑定
      const saved = page.waitForResponse(
        res =>
          res.url().includes(`/agents/declarative/${agent.id}`) &&
          res.request().method() === 'PATCH',
      )
      await dialog.getByRole('button', { name: '保存', exact: true }).click()
      const resp = await saved
      expect(resp.status()).toBe(200)
      expect((await resp.json()).data.bound_skills).toEqual([kept])
      await expect(dialog).toHaveCount(0)
    } finally {
      await page.unroute('**/api/v1/agents/bindable-extensions')
      await deleteAgent(request, token, agent.id)
    }
  })
})