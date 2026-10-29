import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('智能体编辑弹窗绑定拆分', () => {
  test('扩展技能与扩展插件分区可选，搜索与计数生效', async ({ page }) => {
    await login(page)
    await page.goto('/agents/custom')

    const moreBtn = page.getByRole('button', { name: '更多操作' }).first()
    await expect(moreBtn).toBeVisible()
    await moreBtn.click()
    await page.getByRole('menuitem', { name: '编辑', exact: true }).click()

    const dialog = page.getByRole('dialog', { name: /编辑智能体|新建智能体/ })
    await expect(dialog).toBeVisible()

    // 两个独立分区
    const skillItem = dialog.locator('.el-form-item').filter({ hasText: '扩展技能' })
    const pluginItem = dialog.locator('.el-form-item').filter({ hasText: '扩展插件' })
    await expect(skillItem).toBeVisible()
    await expect(pluginItem).toBeVisible()

    // 技能区列出系统已安装的技能（演示小院含 12 个内置农业技能）
    const skillNames = skillItem.locator('.ext-name')
    await expect(skillNames.first()).toBeVisible()
    const skillTotal = await skillNames.count()
    expect(skillTotal).toBeGreaterThanOrEqual(5)

    // 分区搜索框生效：无匹配时显示空状态
    await skillItem.locator('.ext-search input').fill('肯定不存在的技能xyz')
    await expect(skillItem.locator('.ext-empty')).toBeVisible()
    await skillItem.locator('.ext-search input').fill('')

    // 插件区同样可读（演示小院至少有 1 个已启用插件）
    await expect(pluginItem.locator('.ext-name').first()).toBeVisible()

    // 勾选一个技能 → 计数变化；取消不保存，避免污染演示数据
    const before = await skillItem.locator('.ext-count').innerText()
    await skillNames.first().click()
    await expect(skillItem.locator('.ext-count')).not.toHaveText(before)
    await dialog.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toHaveCount(0)
  })
})
