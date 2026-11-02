import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.46 指挥官进化报告与提示词模块：
 * a. 指挥台可查看日复盘、周优化报告；
 * b. 月度迭代可生成/查看，同月幂等且保存迭代前快照；
 * c. 设置页可勾选提示词模块并预览系统提示词。
 */

test.describe('指挥官进化报告与提示词模块（v3.46）', () => {
  test('日复盘/周优化/月度迭代与提示词预览可用', async ({ page }) => {
    await login(page)

    const navItem = page.locator('.nav-item', { hasText: '指挥官工作台' }).first()
    await expect(navItem).toBeVisible()
    await navItem.click()
    await expect(page).toHaveURL(/\/commander$/)

    const shell = page.locator('.commander-page')
    await expect(shell).toBeVisible()
    const evolution = shell.locator('.panel.evolution-panel')
    await expect(evolution).toBeVisible()
    await expect(evolution).toContainText('进化报告')

    await evolution.getByRole('button', { name: '日复盘' }).click()
    const dailyDialog = page.locator('.el-dialog', { hasText: '日复盘' })
    await expect(dailyDialog).toBeVisible()
    await expect(dailyDialog.locator('.report-body')).toContainText('每日复盘')
    await page.keyboard.press('Escape')
    await expect(dailyDialog).toBeHidden()

    await evolution.getByRole('button', { name: '周优化' }).click()
    const weeklyDialog = page.locator('.el-dialog', { hasText: '周优化报告' })
    await expect(weeklyDialog).toBeVisible()
    await expect(weeklyDialog.locator('.report-body')).toContainText('每周优化报告')
    await page.keyboard.press('Escape')
    await expect(weeklyDialog).toBeHidden()

    await evolution.getByRole('button', { name: '生成月度迭代' }).click()
    const confirm = page.locator('.el-message-box')
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('迭代前快照')

    const [iterationResp] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'POST' &&
          resp.url().includes('/api/v1/commander/iterations/run'),
        { timeout: 60_000 },
      ),
      confirm.getByRole('button', { name: '开始生成' }).click(),
    ])
    expect(iterationResp.ok()).toBeTruthy()
    const iterationBody = (await iterationResp.json()) as {
      data: { iteration: { version_label: string; month_key: string }; created: boolean }
    }
    expect(iterationBody.data.iteration.version_label).toMatch(/^V-\d{6}-\d+$/)
    await expect(
      page.locator('.el-message', { hasText: /月度迭代已生成|该月份已有迭代版本/ }),
    ).toBeVisible()

    const iterationDialog = page.locator('.el-dialog', { hasText: '月度迭代' })
    await expect(iterationDialog).toBeVisible()
    await expect(iterationDialog.locator('.report-body')).toContainText('月度迭代报告')
    await page.keyboard.press('Escape')
    await expect(iterationDialog).toBeHidden()

    await page.reload()
    await expect(shell).toBeVisible()
    const refreshed = shell.locator('.panel.evolution-panel')
    const iterationRow = refreshed.locator('.iteration-row').first()
    await expect(iterationRow).toBeVisible()
    await expect(iterationRow).toContainText(iterationBody.data.iteration.month_key)
    await iterationRow.getByRole('button', { name: '查看报告' }).click()
    const reopened = page.locator('.el-dialog', { hasText: '月度迭代' })
    await expect(reopened).toBeVisible()
    await expect(reopened.locator('.report-body')).toContainText('版本变更说明')
    await page.keyboard.press('Escape')
    await expect(reopened).toBeHidden()

    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings$/)
    const commanderTab = page.getByRole('tab', { name: '指挥官' })
    await expect(commanderTab).toBeVisible()
    await commanderTab.click()

    const promptCard = page.locator('.el-card', { hasText: '提示词模块' }).first()
    await expect(promptCard).toBeVisible()
    await expect(promptCard.getByText('交付优先', { exact: true })).toBeVisible()
    await expect(promptCard.getByText('成本纪律', { exact: true })).toBeVisible()
    await expect(promptCard.getByText('简洁表达', { exact: true })).toBeVisible()
    await expect(promptCard.getByText('风险预防', { exact: true })).toBeVisible()

    const [previewResp] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'GET' &&
          resp.url().includes('/api/v1/commander/prompt-preview'),
        { timeout: 30_000 },
      ),
      promptCard.getByRole('button', { name: '预览提示词' }).click(),
    ])
    expect(previewResp.ok()).toBeTruthy()
    const previewDialog = page.locator('.el-dialog', { hasText: '规划系统提示词预览' })
    await expect(previewDialog).toBeVisible()
    await expect(previewDialog.locator('.prompt-preview-body')).toContainText('硬性要求')
    await expect(previewDialog).toContainText('字符')
  })
})
