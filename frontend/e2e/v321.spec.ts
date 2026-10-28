import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** v3.21 信息架构与自生长管理：中文技能状态、详情、自动升级开关、群聊加人搜索、审核页收敛。 */

test.describe('自生长管理（v3.21）', () => {
  test('技能列表：中文状态、点击名称看详情、自动升级可开关', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/evolution')

    await expect(page.getByRole('heading', { name: '自生长管理' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '技能' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: '自动升级' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: /近 30 天调用/ })).toBeVisible()

    const body = page.locator('.el-table__body')
    await expect(body.getByText('已发布').first()).toBeVisible()
    await expect(body.getByText('published')).toHaveCount(0)

    // 点技能名称/编码打开详情抽屉
    const cell = page.locator('.skill-cell').first()
    const name = (await cell.locator('.skill-name').innerText()).split('\n')[0].trim()
    await cell.click()
    const drawer = page.locator('.el-drawer')
    await expect(drawer).toBeVisible()
    await expect(drawer.locator('.detail-name')).toHaveText(name)
    await expect(drawer.getByText('能做什么')).toBeVisible()
    await expect(drawer.getByText('执行提示词')).toBeVisible()

    // 自动升级设置：门槛/冷却/立即升级，保存后提示
    await drawer.getByRole('button', { name: '自动升级设置' }).click()
    const dialog = page.getByRole('dialog', { name: /自动升级：/ })
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('只会生成「待审核」草稿')
    await expect(dialog.getByRole('button', { name: '立即尝试升级' })).toBeVisible()
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('自动升级设置已保存')).toBeVisible()
    await expect(dialog).toBeHidden()

    // 列表开关可切换，并明确告知只产草稿
    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()
    const sw = page.locator('.auto-cell .el-switch').first()
    const wasOn = await sw.evaluate(el => el.classList.contains('is-checked'))
    await sw.click()
    await expect(
      page.getByText(wasOn ? '已关闭自动升级' : '已开启自动升级：只生成待审核草稿'),
    ).toBeVisible()
  })

  test('专家审核只保留知识审核，并引导到自生长管理', async ({ page }) => {
    await login(page)
    await page.goto('/review')

    await expect(page.getByRole('heading', { name: '专家审核' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Skill 管理' })).toHaveCount(0)
    await expect(page.getByText('技能的新建、审核与自动升级已并入')).toBeVisible()

    await page.getByRole('button', { name: '自生长管理' }).click()
    await expect(page).toHaveURL(/\/evolution$/)
    await expect(page.getByRole('heading', { name: '自生长管理' })).toBeVisible()
  })
})

test.describe('群聊加人弹窗（v3.21）', () => {
  test('弹窗支持关键词搜索、分组展示与连续添加', async ({ page }) => {
    await login(page)
    await page.goto('/chat')

    await page.getByRole('button', { name: '向对话添加群聊成员' }).click()
    const dialog = page.getByRole('dialog', { name: '添加对话成员' })
    await expect(dialog).toBeVisible()

    const search = dialog.getByLabel('搜索可添加的助手或班组')
    await expect(search).toBeVisible()
    await search.fill('zzzz-不存在')
    await expect(dialog.getByText('没有匹配的助手，换个关键词试试')).toBeVisible()

    await search.fill('')
    const rows = dialog.locator('.cand-item')
    await expect(rows.first()).toBeVisible()
    // 取第一个还没加入的候选（按索引取，加入后不会被过滤掉导致定位漂移）
    let target = rows.first()
    const total = await rows.count()
    for (let i = 0; i < total; i++) {
      const row = rows.nth(i)
      if (!(await row.evaluate(el => el.classList.contains('joined')))) {
        target = row
        break
      }
    }
    await target.getByRole('button', { name: '添加' }).click()
    await expect(target.getByRole('button', { name: '已在群聊' })).toBeVisible()

    // 添加后弹窗不关闭：允许连续拉多人
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('.footer-tip')).toContainText('可连续添加多位')
  })
})
