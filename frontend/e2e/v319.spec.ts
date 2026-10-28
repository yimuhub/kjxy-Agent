import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** v3.19 离线写入队列：断网时保存到本机，恢复联网后自动补发。 */

test.describe('离线台账（v3.19）', () => {
  test('断网保存地块进入本机队列，恢复联网后同步并落库', async ({ page, context }) => {
    await login(page)
    await page.goto('/farm')
    await expect(page.getByRole('heading', { name: '农业台账' })).toBeVisible()

    const name = `离线地块 ${Date.now()}`

    // 先打开表单，再断网提交：写请求网络不可达 → 落本机队列
    await context.setOffline(true)
    await page.getByRole('button', { name: '新增地块' }).click()
    await page.getByPlaceholder('如：东一号田').fill(name)
    await page.getByRole('button', { name: '保存' }).click()

    await expect(page.getByText('已保存到本机，联网后自动同步')).toBeVisible()
    await expect(page.locator('.offline-bar')).toContainText('离线待同步 1 条')

    // 恢复联网：online 事件自动补发，横幅随即消失
    await context.setOffline(false)
    await expect(page.locator('.offline-bar')).toHaveCount(0, { timeout: 10_000 })

    await page.reload()
    await page.getByRole('tab', { name: '地块与周期' }).click()
    await expect(page.getByText(name, { exact: true }).first()).toBeVisible()
  })

  test('离线时农事记录同样入队，联网后出现在列表里', async ({ page, context }) => {
    await login(page)
    await page.goto('/farm')

    // 准备一个地块（在线）
    const plotName = `离线记录地块 ${Date.now()}`
    await page.getByRole('button', { name: '新增地块' }).click()
    await page.getByPlaceholder('如：东一号田').fill(plotName)
    await page.getByRole('button', { name: '保存' }).click()
    await expect(page.getByText('地块已创建')).toBeVisible()

    const title = `断网施肥 ${Date.now()}`
    await context.setOffline(true)
    await page.getByRole('button', { name: '记一笔' }).first().click()
    const dialog = page.locator('.el-dialog').filter({ hasText: '记一笔农事' })
    await dialog.getByPlaceholder('如：追施分蘖肥').fill(title)
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.locator('.offline-bar')).toContainText('离线待同步 1 条')

    await context.setOffline(false)
    await expect(page.locator('.offline-bar')).toHaveCount(0, { timeout: 10_000 })

    await page.reload()
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible()
  })
})

test.describe('离线冲突合并（v3.19）', () => {
  test('服务器版本更新后同步报冲突，可选择用我的版本覆盖', async ({ page, context, request }) => {
    await login(page)
    const token = await page.evaluate(() => localStorage.getItem('ky_token') ?? '')
    const name = `冲突地块 ${Date.now()}`
    const created = await request.post('/api/v1/farm/plots', {
      headers: { Authorization: `Bearer ${token}` },
      data: { name, crop: '小麦', notes: '初始备注' },
    })
    expect(created.ok()).toBeTruthy()
    const plot = (await created.json()).data as { plot_id: string; revision: number }

    await page.goto('/farm')
    await page.getByRole('tab', { name: '地块与周期' }).click()
    const card = page.locator('.plot-card').filter({ hasText: name })

    // 断网编辑并保存：进入本机队列（携带旧版本 revision）
    await context.setOffline(true)
    await card.getByRole('button', { name: '编辑' }).click()
    const dialog = page.locator('.el-dialog').filter({ hasText: '编辑地块' })
    await dialog.locator('textarea').fill('我的离线备注')
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.locator('.offline-bar')).toContainText('离线待同步 1 条')

    // 另一台设备已把服务器版本推进到 revision+1
    const other = await request.patch(`/api/v1/farm/plots/${plot.plot_id}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { expected_revision: plot.revision, notes: '别人改的备注' },
    })
    expect(other.ok()).toBeTruthy()

    // 恢复联网：自动重放收到 409，横幅转为冲突
    await context.setOffline(false)
    await expect(page.locator('.offline-bar')).toContainText('冲突', { timeout: 10_000 })
    await page.locator('.offline-bar').getByRole('button', { name: '查看明细' }).click()
    await expect(page.getByText('服务器上的记录已被他人修改')).toBeVisible()

    // 用我的版本覆盖：补发后服务器上是自己的备注
    await page.getByRole('button', { name: '用我的覆盖' }).click()
    await expect(page.locator('.offline-bar')).toHaveCount(0, { timeout: 10_000 })

    const after = await request.get('/api/v1/farm/plots', {
      headers: { Authorization: `Bearer ${token}` },
    })
    const rows = (await after.json()).data as { plot_id: string; notes: string; revision: number }[]
    const mine = rows.find(row => row.plot_id === plot.plot_id)
    expect(mine?.notes).toBe('我的离线备注')
    expect(mine?.revision).toBe(plot.revision + 2)
  })
})
