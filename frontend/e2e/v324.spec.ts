import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.24 权限与千人千面：
 * 角色与权限全中文 + 保护型权限二次确认、成员逐人授权、工作台视图默认值、无权限入口全局隐藏。
 */

test.describe('权限与千人千面（v3.24）', () => {
  test('角色与权限：权限点全中文，受保护权限保存前二次确认', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    // 自定义角色上限 20 个：清掉历史运行留下的 e27_perm_*，避免跨轮次累积把这一轮挤爆
    const token = await page.evaluate(() => localStorage.getItem('ky_token'))
    const headers = { Authorization: `Bearer ${token}` }
    const res = await page.request.get('/api/v1/yards/current/roles', { headers })
    const existing = ((await res.json()).data?.roles ?? []) as Array<{ code?: string }>
    for (const row of existing) {
      if (typeof row.code === 'string' && row.code.startsWith('e27_perm_')) {
        await page.request.delete(`/api/v1/yards/current/roles/${row.code}`, { headers })
      }
    }
    await page.goto('/settings?tab=roles')

    const card = page.getByText('角色与权限').first()
    await expect(card).toBeVisible()
    // 矩阵里出现中文标签，且不再裸露权限点编码
    await expect(page.getByText('查看知识库').first()).toBeVisible()
    await expect(page.locator('.perm-label').filter({ hasText: /:/ })).toHaveCount(0)

    const code = `e27_perm_${Date.now().toString(36).slice(-6)}`
    await page.getByRole('button', { name: '新建自定义角色' }).click()
    const dialog = page.getByRole('dialog', { name: '新建自定义角色' })
    await dialog.locator('input').first().fill(code)
    await dialog.locator('input').nth(1).fill('E2E 权限角色')
    await dialog.locator('label.el-checkbox', { hasText: '查看敏感数据' }).click()
    await dialog.getByRole('button', { name: '创建' }).click()

    const confirm = page.getByRole('dialog', { name: '保护型权限二次确认' })
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('查看敏感数据')
    await confirm.getByRole('button', { name: '确认保存' }).click()
    await expect(page.getByText('已创建').first()).toBeVisible()
    await page.request.delete(`/api/v1/yards/current/roles/${code}`, { headers })
  })

  test('成员管理：逐人授权弹窗可保存并即时生效', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/members')

    const row = page.locator('.el-table tbody tr').filter({ hasText: 'operator' }).first()
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: '权限' }).click()

    const dialog = page.getByRole('dialog', { name: '成员权限与逐人授权' })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('有效权限 = 角色默认权限')
    await expect(dialog.getByText('角色自带').first()).toBeVisible()
    await dialog.locator('label.el-checkbox', { hasText: '管理成员' }).click()
    await dialog.getByRole('button', { name: '保存授权' }).click()
    await expect(page.getByText('成员权限已更新')).toBeVisible()
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(dialog).toBeHidden()
  })

  test('工作台视图：设置角色默认密度并回显', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=menu')

    const pane = page.getByRole('tabpanel', { name: '工作台视图' })
    await expect(pane.getByText('全局默认视图')).toBeVisible()
    await pane.locator('label.el-radio-button', { hasText: '极简模式' }).click()
    await pane.getByRole('button', { name: '保存默认视图' }).click()
    await expect(page.getByText('默认工作台视图已保存')).toBeVisible()

    await page.reload()
    await expect(
      page.getByRole('tabpanel', { name: '工作台视图' }).locator('label.is-active'),
    ).toContainText('极简模式')

    // 收尾：把默认视图改回标准，避免污染同一库里的其他用例
    await pane.locator('label.el-radio-button', { hasText: '标准模式' }).click()
    await pane.getByRole('button', { name: '保存默认视图' }).click()
    await expect(page.getByText('默认工作台视图已保存')).toBeVisible()
  })

  test('工作台视图：配置未到时不展示可编辑表单（避免默认值被当成真实配置保存）', async ({ page }) => {
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    await page.route('**/api/v1/yards/current/menu', async (route) => {
      await gate
      await route.continue()
    })

    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=menu')

    const pane = page.getByRole('tabpanel', { name: '工作台视图' })
    // 面板已渲染但配置还在路上：此时不能出现可编辑表单，否则晚到的配置会覆盖管理员的选择
    await expect(pane.getByText('优先级：成员本地自定义')).toBeVisible()
    await expect(pane.locator('label.el-radio-button')).toHaveCount(0)
    await expect(pane.getByRole('button', { name: '保存默认视图' })).toBeDisabled()

    release()
    await expect(pane.locator('label.el-radio-button')).toHaveCount(3)
    await expect(pane.getByRole('button', { name: '保存默认视图' })).toBeEnabled()
  })

  test('千人千面：无权限成员看不到入口，直连被拦到无权限页', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('operator')
    await page.locator('input[autocomplete="current-password"]').fill('operator123')
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page).toHaveURL(/\/(chat)?$/)

    const nav = page.locator('#main-nav')
    await expect(nav.getByText('智能对话')).toBeVisible()
    await expect(nav.getByText('成员管理')).toHaveCount(0)
    await expect(nav.getByText('系统设置')).toHaveCount(0)

    await page.goto('/members')
    await expect(page.getByText('没有访问权限')).toBeVisible()
  })
})
