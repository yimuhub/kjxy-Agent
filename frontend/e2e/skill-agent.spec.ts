import { expect, test, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * 一句话创建技能（v3.2）端到端。
 *
 * 分工：
 * - 检索候选、归属视图、对话技能卡片走真实后端（无模型调用，秒级返回）；
 * - 检索/生成/安装/审核在部分用例里被拦截，避免 60s 级模型往返与演示数据污染。
 */

/** 与后端 /market/agent/request 契约一致的候选载荷 */
const MATCH = {
  item_key: 'builtin:skill:agri_pest_diagnosis',
  name: '病虫害识别流程',
  summary: '依据症状与发生条件给出病虫害判定与用药建议。',
  source: 'builtin',
  license: 'MIT',
  risk_level: 'low',
  installed: false,
  score: 0.86,
}

/** 与后端 /market/agent/generate 契约一致的生成结果 */
const GENERATED = {
  skill: {
    id: 'skill-e2e-generated',
    code: 'e2e_demo_skill',
    name: '示例生成技能',
    version: '1.0.0',
    visibility: 'private',
    status: 'draft',
  },
  spec: { name: '示例生成技能', code: 'e2e_demo_skill', guardrails: { risk_level: 'low' } },
  sources: [{ title: '示例资料', url: 'https://example.com/agri-skill' }],
  requires_review: false,
  note: '',
  token_usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 },
}

/** 打开一句话创建技能向导并输入一句话 */
async function openWizard(page: Page, utterance: string) {
  await page.goto('/library')
  await page.getByRole('button', { name: '一句话创建技能' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('一句话描述需要的技能').fill(utterance)
  return dialog
}

test.describe('一句话创建技能', () => {
  test('检索命中内置技能，展示许可、风险与匹配度', async ({ page }) => {
    await login(page)
    const dialog = await openWizard(page, '我需要一个病虫害防治的技能')
    await dialog.getByRole('button', { name: '检索候选' }).click()

    const items = dialog.locator('.match-item')
    await expect(items.first()).toBeVisible({ timeout: 20_000 })
    expect(await items.count()).toBeGreaterThan(0)

    const first = items.first()
    await expect(first.locator('.match-name')).not.toBeEmpty()
    await expect(first.locator('.match-meta')).toContainText('许可：')
    await expect(first.locator('.match-meta')).toContainText('风险：')
    await expect(first.locator('.match-meta')).toContainText('匹配度：')
  })

  test('安装候选后提示进入专家审核', async ({ page }) => {
    await page.route('**/api/v1/market/agent/request', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { utterance: '我需要一个病虫害防治的技能', recommend: 'install', matches: [MATCH] },
        }),
      }),
    )
    await page.route('**/api/v1/market/agent/install', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { item_key: MATCH.item_key, installed_as: MATCH.item_key, needs_review: true },
        }),
      }),
    )

    await login(page)
    const dialog = await openWizard(page, '我需要一个病虫害防治的技能')
    await dialog.getByRole('button', { name: '检索候选' }).click()

    const item = dialog.locator('.match-item').first()
    await expect(item).toBeVisible()
    await item.getByRole('button', { name: '安装' }).click()

    await expect(dialog.locator('.note').filter({ hasText: '等待专家审核' })).toBeVisible()
    await expect(item.getByRole('button', { name: '已安装' })).toBeDisabled()
  })

  test('无候选时直接生成私有草稿，可提交审核', async ({ page }) => {
    await page.route('**/api/v1/market/agent/request', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { utterance: '我需要一个量子纠缠番茄嫁接技能', recommend: 'generate', matches: [] },
        }),
      }),
    )
    await page.route('**/api/v1/market/agent/generate', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: GENERATED }),
      }),
    )
    await page.route('**/api/v1/skills/*/review', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: { ...GENERATED.skill, status: 'pending_review' } }),
      }),
    )

    await login(page)
    const dialog = await openWizard(page, '我需要一个量子纠缠番茄嫁接技能')
    await dialog.getByRole('button', { name: '检索候选' }).click()

    // 无候选 → 向导自动进入生成阶段并落到结果卡
    const result = dialog.locator('.result-card')
    await expect(result).toBeVisible({ timeout: 20_000 })
    await expect(result.locator('.result-name')).toHaveText('示例生成技能')
    await expect(result.locator('.result-code')).toContainText('e2e_demo_skill')
    await expect(result.locator('.badges')).toContainText('私有')
    await expect(result.locator('.badges')).toContainText('草稿')
    await expect(dialog.locator('.source-list a')).toHaveAttribute(
      'href',
      'https://example.com/agri-skill',
    )

    await result.getByRole('button', { name: '提交审核' }).click()
    await expect(dialog.locator('.note').filter({ hasText: '已提交审核' })).toBeVisible()
    await expect(result.getByRole('button', { name: '已提交审核' })).toBeDisabled()
  })

  test('生成失败时如实展示原因并可重试', async ({ page }) => {
    await page.route('**/api/v1/market/agent/request', route =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: { utterance: '我需要一个量子纠缠番茄嫁接技能', recommend: 'generate', matches: [] },
        }),
      }),
    )
    await page.route('**/api/v1/market/agent/generate', route =>
      route.fulfill({
        status: 429,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'SKILL_QUOTA_EXCEEDED',
            message: '今日技能生成配额已用尽。',
            details: {},
          },
        }),
      }),
    )

    await login(page)
    const dialog = await openWizard(page, '我需要一个量子纠缠番茄嫁接技能')
    await dialog.getByRole('button', { name: '检索候选' }).click()

    const alert = dialog.locator('.el-alert')
    await expect(alert).toBeVisible({ timeout: 20_000 })
    await expect(alert).toContainText('今日技能生成配额已用尽。')
    await expect(dialog.getByRole('button', { name: '重试' })).toBeVisible()
  })
})

test.describe('技能库归属视图', () => {
  test('我的 / 小院 / 平台三级视图可切换且各自取数', async ({ page }) => {
    await login(page)
    await page.goto('/library')

    // 默认停在「市场」检索区，切到归属视图后才出现技能面板
    await page.getByRole('tab', { name: '我的' }).click()
    const scopePanel = page.locator('.scope-panel')
    await expect(scopePanel).toBeVisible({ timeout: 20_000 })
    await expect(scopePanel).toContainText('我创建的技能')

    await page.getByRole('tab', { name: '小院' }).click()
    await expect(scopePanel).toContainText('小院共享的技能')

    await page.getByRole('tab', { name: '平台' }).click()
    await expect(scopePanel).toContainText('平台共享的技能')
  })
})

test.describe('技能生成设置', () => {
  test('一句话生成配置可读可存，开关落在 skill_lab 设置项', async ({ page }) => {
    let payload: Record<string, unknown> | null = null
    await page.route('**/api/v1/yards/current/settings/skill_lab', route => {
      if (route.request().method() === 'PUT') {
        payload = route.request().postDataJSON()
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: payload }),
        })
        return
      }
      route.fallback()
    })

    await login(page)
    await page.goto('/settings?tab=network')

    const card = page.locator('.settings-skill-lab')
    await expect(card).toBeVisible({ timeout: 20_000 })
    await expect(card).toContainText('一句话技能生成')

    // 表单回填后端下发的 skill_lab 默认值
    const dailyLimit = card
      .locator('.el-form-item')
      .filter({ hasText: '每日生成上限' })
      .locator('input')
    await expect(dailyLimit).toHaveValue('20')

    // 关闭开关后保存，请求体落到 skill_lab 设置项
    await card
      .locator('.el-form-item')
      .filter({ hasText: '启用一句话生成' })
      .locator('.el-switch')
      .click()
    await card.getByRole('button', { name: '保存' }).click()

    await expect.poll(() => payload).not.toBeNull()
    expect(payload).toMatchObject({
      enabled: false,
      daily_limit: 20,
      use_web: true,
      max_sources: 6,
    })
  })
})

test.describe('对话内技能卡片', () => {
  test('一句话要技能命中候选，卡片可安装', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()
    await expect(page.locator('.chat-empty')).toBeVisible()

    await page.locator('textarea').fill('我需要一个病虫害防治的技能')
    await page.locator('textarea').press('Enter')

    const card = page.locator('.skill-card').last()
    await expect(card).toBeVisible({ timeout: 60_000 })
    await expect(card.locator('.kind')).toHaveText('技能卡片')
    await expect(card.locator('.utterance')).toContainText('病虫害防治')
    await expect(card.locator('.match-item').first()).toBeVisible()
    await expect(card.locator('.match-meta')).toContainText('风险：')
  })
})
