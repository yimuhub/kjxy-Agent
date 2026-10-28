import { expect, type Page } from '@playwright/test'

/** 演示小院管理员登录（依赖 make seed-dev 创建的种子账号） */
export async function login(
  page: Page,
  username = 'yard_admin',
  password = 'yard123',
) {
  await page.goto('/login')
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.locator('.el-breadcrumb')).toContainText('演示科技小院')
}

/**
 * 侧栏当前视图里的会话条数。
 *
 * 列表做了窗口化（一次只渲染前 60 行，滚动再放下一页），DOM 行数会封顶，
 * 所以条数改读容器上的 data-session-total——它始终等于模型里的真实条数。
 */
export async function sessionTotal(page: Page): Promise<number> {
  const raw = await page.locator('.session-list').getAttribute('data-session-total')
  if (raw === null) throw new Error('找不到 .session-list[data-session-total]')
  return Number(raw)
}
