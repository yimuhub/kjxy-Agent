import { expect, test } from '@playwright/test'
import { login, sessionTotal } from './helpers'

/**
 * 会话列表时间（v3.14.0）：每一行都显示「建会话那一刻」的年-月-日 时:分，
 * 不再是相对时间，也不再因为后端下发裸 UTC 而整体早 8 小时。
 */
const pad = (n: number) => String(n).padStart(2, '0')

function stamp(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

test('新建对话后列表显示本地创建时刻（年-月-日 时:分）', async ({ page }) => {
  test.setTimeout(60_000)
  const errors: string[] = []
  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text())
  })
  page.on('pageerror', err => errors.push(err.message))

  await login(page)
  await page.goto('/chat')
  const firstTime = page.locator('.session-item').first().locator('.s-time')
  await expect(firstTime).toBeVisible()
  // 列表窗口化后 DOM 行数封顶，条数一律读容器上的模型总数
  const before = await sessionTotal(page)

  const justNow = new Date()
  const allowed = [stamp(justNow), stamp(new Date(justNow.getTime() + 60_000))]
  await page.locator('.new-btn').click()
  // 新建的会话排在最前：它的时刻就是本机此刻（裸 UTC 会被算成早 8 小时）
  await expect(firstTime).toHaveText(new RegExp(`^(${allowed.join('|')})$`), { timeout: 15_000 })
  await expect.poll(() => sessionTotal(page)).toBe(before + 1)

  // 清理本次用例创建的会话，不往演示数据里堆垃圾（v3.15：删除收进三点菜单）
  // 菜单弹出层带会话 id 的专属 class，点的就是这一行自己的那一份
  const firstRow = page.locator('.session-item').first()
  const sid = await firstRow.getAttribute('data-session-id')
  if (!sid) throw new Error('会话行缺少 data-session-id')
  await firstRow.locator('.s-more').click()
  await page.locator(`.s-menu-${sid}`).getByRole('menuitem', { name: '删除', exact: true }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '删除' }).click()
  await expect.poll(() => sessionTotal(page)).toBe(before)

  expect(errors).toEqual([])
})
