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

    // 折叠态只渲染前 3 行
    await expect(skillItem.locator('.ext-name').first()).toBeVisible()

    // 「展开全部」把完整列表放进 teleport 到 body 的弹层，断言必须落在弹层内
    const expand = skillItem.getByRole('button', { name: /展开全部/ })
    await expect(expand).toBeVisible()
    await expand.click()
    const popover = page.locator('.ext-popover')
    await expect(popover).toBeVisible()
    const popoverNames = popover.locator('.ext-name')
    expect(await popoverNames.count()).toBeGreaterThanOrEqual(5)

    // 分区搜索框生效：无匹配时显示空状态
    const searchInput = popover.locator('.ext-search input')
    await searchInput.fill('肯定不存在的技能xyz')
    await expect(popover.locator('.ext-empty')).toBeVisible()
    await searchInput.fill('')
    await expect(popoverNames.first()).toBeVisible()

    // 收起弹层，回到表单项内操作
    await skillItem.getByRole('button', { name: /收起全部/ }).click()
    await expect(popover).toBeHidden()

    // 插件区要么列出已安装插件，要么给出可操作的空态指引（新库未必装了插件）
    const pluginEmpty = pluginItem.getByText(/暂无可绑定的扩展插件/)
    if (await pluginEmpty.isVisible()) {
      await expect(pluginItem.getByRole('button', { name: '去插件与技能库' })).toBeVisible()
    } else {
      await expect(pluginItem.locator('.ext-name').first()).toBeVisible()
    }

    // 勾选一个技能 → 计数变化；取消不保存，避免污染演示数据
    const before = await skillItem.locator('.ext-count').innerText()
    await skillItem.locator('.ext-name').first().click()
    await expect(skillItem.locator('.ext-count')).not.toHaveText(before)
    await dialog.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toHaveCount(0)
  })
})
