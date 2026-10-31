import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * 工厂蓝图展示（3.34）：不触发真实生成，拦截运行列表与详情，
 * 断言阶段工作流带、角色阶段徽标、联网情报来源与班组说明。
 */
test.describe('Agent 工厂多角色工作流', () => {
  test('蓝图展示阶段、情报与审核角色', async ({ page }) => {
    await login(page)

    const run = {
      id: 'run-334',
      status: 'completed',
      industry_brief: '山区果园通过电商卖苹果，做品牌和渠道',
      generated_by: 'heuristic',
      error: null,
      created_by: 'u-1',
      approved_by: null,
      created_at: '2026-10-31T08:00:00Z',
      updated_at: '2026-10-31T08:01:00Z',
      role_count: 3,
      needs_knowledge_count: 0,
      research_used: true,
      research_sources: 1,
    }

    const agents = [
      {
        agent_code: 'ind_a1',
        name: '市场行情分析师（果园、苹果）',
        emoji: '📈',
        description: '分析价格与渠道结构',
        persona_prompt: '你是市场分析师',
        task_prompt_template: '请回答：{{question}}',
        allowed_tools: ['knowledge.search'],
        knowledge_scopes: ['yard'],
        suggested_questions: ['行情怎么样？'],
        agent_id: 'a1',
        knowledge_coverage: 2,
        needs_knowledge: false,
        smoke_questions: ['行情怎么样？'],
        risk_level: 'low',
        status: 'draft',
        stage: 'analysis',
        suggested_skills: ['office_excel_table'],
      },
      {
        agent_code: 'ind_a2',
        name: '渠道运营师（果园、苹果）',
        emoji: '🛒',
        description: '把销售策略落成渠道动作',
        persona_prompt: '你是渠道运营',
        task_prompt_template: '请回答：{{question}}',
        allowed_tools: ['knowledge.search'],
        knowledge_scopes: ['yard'],
        suggested_questions: ['电商怎么起步？'],
        agent_id: 'a2',
        knowledge_coverage: 1,
        needs_knowledge: false,
        smoke_questions: ['电商怎么起步？'],
        risk_level: 'low',
        status: 'draft',
        stage: 'execution',
      },
      {
        agent_code: 'ind_a3',
        name: '质检审核员（果园、苹果）',
        emoji: '✅',
        description: '复核事实、依据与合规风险',
        persona_prompt: '你是审核员',
        task_prompt_template: '请回答：{{question}}',
        allowed_tools: ['knowledge.search'],
        knowledge_scopes: ['yard'],
        suggested_questions: ['结论依据够不够？'],
        agent_id: 'a3',
        knowledge_coverage: 0,
        needs_knowledge: true,
        smoke_questions: ['结论依据够不够？'],
        risk_level: 'low',
        status: 'draft',
        stage: 'review',
      },
    ]

    await page.route('**/api/v1/agents/factory/runs', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: { data: { runs: [run] } } })
        return
      }
      await route.fallback()
    })
    await page.route('**/api/v1/agents/factory/runs/run-334', async route => {
      await route.fulfill({
        json: {
          data: {
            ...run,
            blueprint_json: {
              schema: 'agent_factory_blueprint.v1',
              industry_brief: run.industry_brief,
              focus: null,
              generated_by: 'heuristic',
              role_count: agents.length,
              workflow: {
                mode: 'sequential',
                handoff: '前序输出作为后序输入',
                stages: [
                  { key: 'analysis', label: '分析与取证', roles: ['ind_a1'] },
                  { key: 'execution', label: '执行落地', roles: ['ind_a2'] },
                  { key: 'review', label: '审核把关', roles: ['ind_a3'] },
                ],
              },
              research: {
                used: true,
                provider: 'bing',
                queries: ['果园 苹果 渠道'],
                sources: [
                  {
                    title: '苹果电商渠道打法',
                    url: 'https://example.com/a',
                    snippet: '分级包装与冷链',
                    source: 'bing',
                  },
                ],
                summary: '- 苹果电商渠道打法：分级包装与冷链',
              },
              agents,
            },
          },
        },
      })
    })

    await page.goto('/agents/factory')
    const row = page.locator('.el-table__row', { hasText: run.industry_brief })
    await expect(row).toBeVisible()
    await row.click()

    await expect(page.locator('.bp-title')).toContainText('3 个角色')
    await expect(page.locator('.wf-band')).toContainText('分析与取证')
    await expect(page.locator('.wf-band')).toContainText('执行落地')
    await expect(page.locator('.wf-band')).toContainText('审核把关')
    await expect(page.locator('.bp-meta')).toContainText('联网情报 1 条')

    const reviewCard = page.locator('.role-card', { hasText: '质检审核员' })
    await expect(reviewCard.locator('.role-tags')).toContainText('审核把关')
    await expect(reviewCard.locator('.role-tags')).toContainText('低风险')

    const analysisCard = page.locator('.role-card', { hasText: '市场行情分析师' })
    await expect(analysisCard.locator('.role-tags')).toContainText('Excel 表格')

    await expect(page.locator('.as-team-row .note')).toContainText('按工作流阶段接力')
  })
})
