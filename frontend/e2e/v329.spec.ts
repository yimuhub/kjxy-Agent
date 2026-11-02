import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('v3.29 用量统计与可读性升级', () => {
  test('用量页：趋势图、区间切换、固定高度表格与 CSV 导出', async ({ page }) => {
    await login(page)
    await page.goto('/quota')

    await expect(page.getByRole('heading', { name: '用量额度与 Token 统计' })).toBeVisible()
    // ECharts 复合图（柱 + 折线）已渲染
    await expect(page.locator('.trend-card canvas')).toHaveCount(1)
    await expect(page.getByText('近 30 天累计 Token')).toBeVisible()

    // 区间切换：KPI 文案跟随 7 天窗口
    await page.locator('.el-radio-button', { hasText: '近 7 天' }).click()
    await expect(page.getByText('近 7 天累计 Token')).toBeVisible()

    // 按场景 / 按智能体两张表高度一致（固定 320，超出内部滚动）
    const heights = await page
      .locator('.fixed-card .el-table__body-wrapper')
      .evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().height)))
    expect(heights.length).toBeGreaterThanOrEqual(2)
    expect(heights[0]).toBe(heights[1])
    expect(heights[0]).toBeLessThanOrEqual(330)

    // 一键导出统计 CSV
    const statsDownload = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出统计 CSV' }).click()
    expect((await statsDownload).suggestedFilename()).toMatch(/^token-usage-\d{4}-\d{2}-\d{2}\.csv$/)

    const traceDownload = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出调用流水 CSV' }).click()
    expect((await traceDownload).suggestedFilename()).toMatch(/^llm-traces-\d{4}-\d{2}-\d{2}\.csv$/)
  })

  test('任务中心：列表首列是可读任务名称', async ({ page }) => {
    await login(page)
    await page.goto('/tasks')

    await expect(page.getByRole('columnheader', { name: '任务名称' })).toBeVisible()
    const taskCard = page
      .locator('.el-card')
      .filter({ has: page.getByRole('columnheader', { name: '任务名称' }) })
    const firstTitle = taskCard
      .locator('.el-table__body-wrapper tbody tr')
      .first()
      .locator('td')
      .first()
    await expect(firstTitle).not.toHaveText('')
    await expect(firstTitle.locator('.task-title')).toBeVisible()
  })

  test('新建智能体：按名称/职责自动绑定相关技能', async ({ page }) => {
    await login(page)
    await page.goto('/agents/custom')

    await page.getByRole('button', { name: '+ 新建智能体' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()

    await page.getByPlaceholder('小写字母数字下划线，如 miaoqing_reporter').fill(
      `e2e_maint_${Date.now()}`,
    )
    await page.getByPlaceholder('如：水稻苗情诊断师').fill('农机维修专家')
    await page.getByPlaceholder('解读苗情资料并给出田间管理建议').fill('负责拖拉机、收割机保养与检修')

    const note = page.locator('.auto-skill-note')
    await expect(note).toContainText('自动绑定', { timeout: 15_000 })
    await expect(note).toContainText('农机保养')

    await page.getByRole('button', { name: '取消' }).click()
  })
})
