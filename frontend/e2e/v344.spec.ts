import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.44 指挥官监督测评与进化治理：
 * a. 指挥台展示人格标签与“监督测评”面板；
 * b. 规则测评（use_llm=false）落库后，面板展示周键、五项分项与综合分；
 * c. 完整报告可在弹窗查看；
 * d. 手动快照可创建，回滚前自动备份。
 */

test.describe('指挥官监督测评与快照（v3.44）', () => {
  test('测评报告、人格标签与快照回滚可用', async ({ page }) => {
    await login(page)

    const navItem = page.locator('.nav-item', { hasText: '指挥官工作台' }).first()
    await expect(navItem).toBeVisible()
    await navItem.click()
    await expect(page).toHaveURL(/\/commander$/)

    const shell = page.locator('.commander-page')
    await expect(shell).toBeVisible()
    await expect(shell.locator('.head-tags')).toContainText(/型/)

    const governance = shell.locator('.panel', { hasText: '监督测评' })
    await expect(governance).toBeVisible()
    await expect(governance).toContainText('独立考官按周打分')

    const token = await page.evaluate(() => localStorage.getItem('ky_token'))
    expect(token).toBeTruthy()

    const evalResp = await page.request.post('/api/v1/commander/evaluations/run', {
      headers: { Authorization: `Bearer ${token}` },
      data: { use_llm: false, force: true },
    })
    expect(evalResp.ok()).toBeTruthy()
    const evalBody = (await evalResp.json()) as {
      data: {
        evaluation: {
          week_key: string
          total: number
          scores: Record<string, number>
        }
      }
    }
    const evaluation = evalBody.data.evaluation
    expect(evaluation.week_key).toMatch(/^\d{4}-W\d{2}$/)
    expect(evaluation.total).toBeGreaterThanOrEqual(0)
    expect(Object.keys(evaluation.scores)).toHaveLength(5)

    await page.reload()
    await expect(shell).toBeVisible()
    const refreshed = shell.locator('.panel', { hasText: '监督测评' })
    await expect(refreshed).toContainText(evaluation.week_key)
    await expect(refreshed.locator('.score-chip')).toHaveCount(5)
    await expect(refreshed.locator('.eval-total')).toContainText('综合分')

    await refreshed.getByRole('button', { name: '查看完整报告' }).click()
    const dialog = page.locator('.el-dialog', { hasText: '测评报告' })
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('.report-body')).toContainText('测评')
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()

    const snapshotPanel = shell.locator('.panel', { hasText: '监督测评' })
    const [snapshotResp] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'POST' &&
          resp.url().includes('/api/v1/commander/snapshots'),
        { timeout: 30_000 },
      ),
      snapshotPanel.getByRole('button', { name: '创建快照' }).click(),
    ])
    expect(snapshotResp.ok()).toBeTruthy()
    await expect(snapshotPanel.locator('.snapshot-row').first()).toBeVisible()
    await expect(snapshotPanel.locator('.snapshot-row').first()).toContainText('手动创建')

    await snapshotPanel.getByRole('button', { name: '回滚' }).first().click()
    const confirm = page.locator('.el-message-box')
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('当前状态会先自动备份')

    const [restoreResp] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'POST' &&
          resp.url().includes('/restore'),
        { timeout: 30_000 },
      ),
      confirm.getByRole('button', { name: '确认回滚' }).click(),
    ])
    expect(restoreResp.ok()).toBeTruthy()
    await expect(page.locator('.el-message', { hasText: '已回滚到该快照' })).toBeVisible()
    await expect(
      snapshotPanel.locator('.snapshot-row', { hasText: '回滚前备份' }).first(),
    ).toBeVisible()
  })
})
