import { expect, test } from '@playwright/test'
import { login } from './helpers'

const QUOTA_STATUS = {
  yard_id: 'mock-yard',
  period: '2026-10',
  quotas: {
    llm_calls: {
      label: '模型调用',
      limit: 1000,
      used: 961,
      remaining: 39,
      usage_percent: 96.1,
    },
    knowledge_docs: {
      label: '知识库文档',
      limit: 100,
      used: 0,
      remaining: 100,
      usage_percent: 0,
    },
    tasks: { label: '任务', limit: 500, used: 8, remaining: 492, usage_percent: 1.6 },
  },
  warnings: [
    {
      kind: 'llm_calls',
      label: '模型调用',
      level: 'warning',
      message: '模型调用配额使用率已达 96.1%（961/1000）。',
    },
  ],
}

test.describe('v3.31 用量额度运营化（调整上限 / 清零已用）', () => {
  test('管理员可见额度入口，调整上限时按中文名提交三个限额', async ({ page }) => {
    await login(page)
    let patched: Record<string, number> | null = null
    await page.route('**/api/v1/yards/quota', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: { data: QUOTA_STATUS, request_id: 'e2e' } })
        return
      }
      await route.fallback()
    })
    await page.route('**/api/v1/admin/quotas/*', async route => {
      if (route.request().method() === 'PATCH') {
        patched = route.request().postDataJSON() as Record<string, number>
        await route.fulfill({
          json: {
            data: {
              ...QUOTA_STATUS,
              quotas: {
                ...QUOTA_STATUS.quotas,
                llm_calls: { ...QUOTA_STATUS.quotas.llm_calls, limit: 2000, used: 0 },
              },
              warnings: [],
            },
            request_id: 'e2e',
          },
        })
        return
      }
      await route.fallback()
    })

    await page.goto('/quota')
    // 告警用中文名，不再把 llm_calls 这种码抛给用户
    await expect(page.getByText('模型调用配额使用率已达 96.1%')).toBeVisible()

    await page.getByRole('button', { name: '调整额度' }).click()
    const dialog = page.getByRole('dialog', { name: '调整用量额度' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('模型调用')).toBeVisible()
    await expect(dialog.getByText('知识库文档')).toBeVisible()

    const llmInput = dialog.locator('.el-input-number input').first()
    await llmInput.fill('2000')
    await llmInput.blur()
    await dialog.getByRole('button', { name: '保存' }).click()

    await expect.poll(() => patched?.llm_calls_limit).toBe(2000)
    expect(patched?.knowledge_docs_limit).toBe(100)
    expect(patched?.tasks_limit).toBe(500)
    await expect(page.getByText('额度已更新')).toBeVisible()
  })

  test('清零已用需二次确认，确认后调用 reset 接口', async ({ page }) => {
    await login(page)
    let resetCalls = 0
    await page.route('**/api/v1/yards/quota', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ json: { data: QUOTA_STATUS, request_id: 'e2e' } })
        return
      }
      await route.fallback()
    })
    await page.route('**/api/v1/admin/quotas/*/reset', async route => {
      resetCalls += 1
      await route.fulfill({
        json: {
          data: {
            ...QUOTA_STATUS,
            quotas: {
              llm_calls: { ...QUOTA_STATUS.quotas.llm_calls, used: 0, remaining: 1000 },
              knowledge_docs: QUOTA_STATUS.quotas.knowledge_docs,
              tasks: QUOTA_STATUS.quotas.tasks,
            },
            warnings: [],
          },
          request_id: 'e2e',
        },
      })
    })

    await page.goto('/quota')
    await page.getByRole('button', { name: '清零已用' }).click()
    const box = page.locator('.el-message-box')
    await expect(box).toContainText('清零只重置「已用」计数')
    await box.getByRole('button', { name: '清零' }).click()

    await expect.poll(() => resetCalls).toBe(1)
    await expect(page.getByText('已用量已清零')).toBeVisible()
  })
})
