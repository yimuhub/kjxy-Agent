import { expect, test, type APIRequestContext } from '@playwright/test'
import { login } from './helpers'

/**
 * 智能体「新建 → 绑定大量技能 → 保存」死路回归（v3.78）。
 *
 * 用户实测：新建/编辑助手时多绑几个技能就「没办法确认」。编辑侧已由
 * agent-bind-save.spec.ts 守住；这里补新建侧——真实浏览器走完整创建路径：
 * 填编码/名称/人设 → 展开候选弹层全选 → 收起弹层 → 保存，
 * 断言弹窗关闭、成功提示出现、后端确实落库全部绑定（不再卡死）。
 *
 * 用例自建专属智能体（唯一编码），结束软删除收尾，不污染演示数据。
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

async function deleteAgent(request: APIRequestContext, token: string, id: string): Promise<void> {
  await request.delete(`/api/v1/agents/declarative/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

test.describe('智能体新建绑定技能保存死路回归（v3.78）', () => {
  test('新建时全选大量技能后保存成功，弹窗关闭且后端落库全部绑定', async ({ page, request }) => {
    const token = await authToken(request)
    const skills = await fetchSkills(request, token)
    // 折叠态只渲染前 3 行，候选必须多于 3 才有「展开全部」可点
    expect(skills.length).toBeGreaterThanOrEqual(5)

    await login(page)
    await page.goto('/agents/custom')
    await page.getByRole('button', { name: /新建智能体/ }).first().click()
    const dialog = page.getByRole('dialog', { name: '新建智能体' })
    await expect(dialog).toBeVisible()

    const code = `zz_e2e_new_${Date.now().toString().slice(-6)}${Math.floor(Math.random() * 1000)}`
    await dialog.getByPlaceholder('小写字母数字下划线，如 miaoqing_reporter').fill(code)
    await dialog.getByPlaceholder('如：水稻苗情诊断师').fill(`E2E新建回归${code.slice(-6)}`)
    await dialog
      .getByPlaceholder('你是一名……（角色、专业领域、语气、合规边界）')
      .fill('你是测试助手，依据资料作答。')
    // 任务模板默认已含 {{question}} 占位，无需改动

    const skillItem = dialog.locator('.el-form-item').filter({ hasText: '扩展技能' })
    await skillItem.getByRole('button', { name: /展开全部/ }).click()
    const popover = page.locator('.ext-popover')
    await expect(popover).toBeVisible()
    await skillItem.getByRole('button', { name: '全选', exact: true }).click()
    await expect(skillItem.locator('.ext-count')).toHaveText(`已选 ${skills.length}`)
    await expect(popover.locator('.el-checkbox.is-checked')).toHaveCount(skills.length)

    // 点弹层外（弹窗标题）收起：弹层关掉、外层新建弹窗不被连带关闭
    await dialog.locator('.el-dialog__title').click()
    await expect(popover).toBeHidden()
    await expect(dialog).toBeVisible()

    // 保存：后端确实写入 N 条绑定，弹窗关闭 = 不再「没办法确认」
    const saved = page.waitForResponse(
      res => res.url().includes('/agents/declarative') && res.request().method() === 'POST',
    )
    await dialog.getByRole('button', { name: '保存', exact: true }).click()
    const resp = await saved
    expect(resp.status()).toBe(200)
    const body = await resp.json()
    expect(body.data.bound_skills).toHaveLength(skills.length)
    const createdId = body.data.id as string
    try {
      await expect(
        page.locator('.el-message').filter({ hasText: '智能体草稿已创建' }),
      ).toBeVisible()
      await expect(dialog).toHaveCount(0)
    } finally {
      await deleteAgent(request, token, createdId)
    }
  })
})