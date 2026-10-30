import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.27 诊断材料与设备自动诊断：
 * - 系统设置默认落入「角色与权限」，不再只有标签没有内容；
 * - 侧栏「对话」统一为「智能对话」（负责人全功能视图与管理员标准视图一致）；
 * - 新建诊断可直接上传图片/文档（多选），并引用已接入设备由 Agent 自动诊断。
 */

const PNG = {
  name: 'e2e-leaf.png',
  mimeType: 'image/png',
  // 只需合法的 PNG 文件头，服务端按魔数校验
  buffer: Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(24)]),
}

test.describe('v3.27 系统设置与命名统一', () => {
  test('进入系统设置默认展示角色与权限内容', async ({ page }) => {
    await login(page)
    await page.goto('/settings')

    await expect(page.getByText('系统设置', { exact: true }).first()).toBeVisible()
    // 默认标签必须有内容（历史 bug：默认 tab=config 不存在 → 只有标签）
    await expect(page.getByText('新建自定义角色')).toBeVisible()
    await expect(page.getByRole('tab', { name: '工作台视图' })).toBeVisible()

    await page.getByRole('tab', { name: '工作台视图' }).click()
    await expect(page.getByText('全局默认视图')).toBeVisible()
  })

  test('非法 tab 参数回落到角色与权限而不是空白', async ({ page }) => {
    await login(page)
    await page.goto('/settings?tab=not-a-tab')
    await expect(page.getByText('新建自定义角色')).toBeVisible()
  })

  test('负责人与管理员看到的对话菜单都叫「智能对话」', async ({ page }) => {
    // 管理员：标准视图
    await login(page)
    await expect(page.getByRole('menuitem', { name: '智能对话', exact: true })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '对话', exact: true })).toHaveCount(0)

    // 负责人：全功能视图
    await page.context().clearCookies()
    await page.evaluate(() => localStorage.clear())
    await login(page, 'admin', 'admin123')
    await expect(page.getByRole('menuitem', { name: '智能对话', exact: true })).toBeVisible()
    await expect(page.getByRole('menuitem', { name: '对话', exact: true })).toHaveCount(0)
  })
})

test.describe('v3.27 诊断材料与设备自动诊断', () => {
  test('新建诊断可多选上传图片与文档并留档', async ({ page }) => {
    await login(page)
    await page.goto('/diagnosis')
    await page.getByRole('button', { name: '新建诊断' }).first().click()

    const dialog = page.getByRole('dialog').filter({ hasText: '新建作物诊断' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText('点击或拖拽上传图片 / PDF / 文档（可多选）')).toBeVisible()

    await dialog.locator('input[type=file]').setInputFiles([
      PNG,
      {
        name: 'e2e-notes.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('分蘖期叶片发黄，建议排查根系与肥水。', 'utf-8'),
      },
    ])
    await expect(dialog.locator('.attach-item')).toHaveCount(2, { timeout: 20_000 })
    await expect(dialog.getByText('e2e-leaf.png')).toBeVisible()
    await expect(dialog.getByText('e2e-notes.txt')).toBeVisible()

    const cropInput = dialog.locator('.el-form-item').filter({ hasText: '作物' }).locator('input')
    if (!(await cropInput.inputValue())) await cropInput.fill('水稻')
    await dialog
      .getByPlaceholder(/描述发病部位/)
      .fill('叶片发黄有褐斑，最近连阴雨')
    await dialog.getByRole('button', { name: '生成并留档' }).click()

    const detail = page.getByRole('dialog').filter({ hasText: '现场材料' })
    await expect(detail).toBeVisible({ timeout: 30_000 })
    await expect(detail.getByText('现场材料（2）')).toBeVisible()
    await expect(detail.getByText('e2e-leaf.png')).toBeVisible()
  })

  test('引用已接入设备由 Agent 自动诊断并高亮标记', async ({ page }) => {
    await login(page)
    await page.goto('/diagnosis')
    await page.getByRole('button', { name: '新建诊断' }).first().click()

    const dialog = page.getByRole('dialog').filter({ hasText: '新建作物诊断' })
    const deviceSelect = dialog.locator('.el-form-item').filter({ hasText: '引用设备' })
    await deviceSelect.locator('.el-select').click()
    const option = page.getByRole('option', { name: /^演示气象站（/ })
    await expect(option).toBeVisible({ timeout: 10_000 })
    await option.click()

    await dialog.getByRole('button', { name: '设备自动诊断' }).click()

    const detail = page.getByRole('dialog').filter({ hasText: '专家复核' })
    await expect(detail).toBeVisible({ timeout: 30_000 })
    await expect(detail.getByText(/Agent 自动诊断/).first()).toBeVisible()
    await expect(detail.getByText(/演示气象站/).first()).toBeVisible()
  })
})
