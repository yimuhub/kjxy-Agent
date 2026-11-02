import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.45 经验资产：
 * a. 指挥台展示工作流/失败黑名单/技能三块资产与计数；
 * b. 手动工作流可创建、下线、再启用；
 * c. 扫描沉淀接口可用，页面同步刷新。
 */

test.describe('指挥官经验资产（v3.45）', () => {
  test('资产面板、工作流上下线与扫描沉淀可用', async ({ page }) => {
    await login(page)

    const navItem = page.locator('.nav-item', { hasText: '指挥官工作台' }).first()
    await expect(navItem).toBeVisible()
    await navItem.click()
    await expect(page).toHaveURL(/\/commander$/)

    const shell = page.locator('.commander-page')
    await expect(shell).toBeVisible()
    const assetsPanel = shell.locator('.panel.assets-panel')
    await expect(assetsPanel).toBeVisible()
    await expect(assetsPanel).toContainText('经验资产')
    await expect(assetsPanel.locator('.asset-count')).toHaveCount(3)
    await expect(assetsPanel).toContainText('标准工作流')
    await expect(assetsPanel).toContainText('失败黑名单')
    await expect(assetsPanel).toContainText('生效技能')

    const token = await page.evaluate(() => localStorage.getItem('ky_token'))
    expect(token).toBeTruthy()
    const runKey = Date.now().toString(36)
    const workflowName = `E2E 宣传 PPT 流程 ${runKey}`
    const workflowScene = `给小院做宣传 PPT（E2E ${runKey}）`

    const created = await page.request.post('/api/v1/commander/assets/workflows', {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: workflowName,
        scene: workflowScene,
        preconditions: '已有小院介绍与产品清单',
        steps: [
          {
            title: '整理素材',
            instruction: '汇总小院介绍与产品卖点',
            expected_output: '素材清单',
          },
          {
            title: '生成 PPT',
            instruction: '按素材生成 8 页宣传 PPT',
            expected_output: 'pptx 文件',
          },
        ],
      },
    })
    expect(created.ok()).toBeTruthy()
    const workflowId = ((await created.json()) as { data: { workflow: { id: string } } }).data
      .workflow.id

    await page.reload()
    await expect(assetsPanel).toBeVisible()
    const workflowRow = assetsPanel.locator('.asset-row', { hasText: workflowName })
    await expect(workflowRow).toBeVisible()
    await expect(workflowRow).toContainText('2 步')

    const [archived] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'PUT' &&
          resp.url().includes(`/api/v1/commander/assets/workflows/${workflowId}`),
        { timeout: 30_000 },
      ),
      workflowRow.getByRole('button', { name: '下线' }).click(),
    ])
    expect(archived.ok()).toBeTruthy()
    await expect(
      assetsPanel.locator('.asset-row', { hasText: workflowName }).getByRole('button', {
        name: '启用',
      }),
    ).toBeVisible()

    const [reactivated] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'PUT' &&
          resp.url().includes(`/api/v1/commander/assets/workflows/${workflowId}`),
        { timeout: 30_000 },
      ),
      assetsPanel
        .locator('.asset-row', { hasText: workflowName })
        .getByRole('button', { name: '启用' })
        .click(),
    ])
    expect(reactivated.ok()).toBeTruthy()
    await expect(
      assetsPanel.locator('.asset-row', { hasText: workflowName }).getByRole('button', {
        name: '下线',
      }),
    ).toBeVisible()

    const [scanned] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'POST' &&
          resp.url().includes('/api/v1/commander/assets/scan'),
        { timeout: 60_000 },
      ),
      assetsPanel.getByRole('button', { name: '扫描沉淀' }).click(),
    ])
    expect(scanned.ok()).toBeTruthy()
    const scanBody = (await scanned.json()) as {
      data: { runs_scanned: number; workflows_created: number; counts: { workflows: number } }
    }
    expect(scanBody.data.runs_scanned).toBeGreaterThanOrEqual(0)
    await expect(page.locator('.el-message', { hasText: '已扫描' })).toBeVisible()
  })
})
