import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('认证与导航', () => {
  test('错误密码给出中文提示且停留在登录页', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('tenant_admin')
    await page.locator('input[autocomplete="current-password"]').fill('wrong-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.locator('.el-alert')).toContainText('用户名或密码错误')
    await expect(page).toHaveURL(/\/login/)
  })

  test('登录后导航收敛为 9 个一级入口，二级菜单可跳转', async ({ page }) => {
    await login(page)
    await expect(page).toHaveTitle('概览 · 阗策 · 科技小院')

    const topLevel = page.locator(
      '#main-nav > .el-menu > .el-menu-item-group > ul > li',
    )
    await expect(topLevel).toHaveCount(9)

    await page.getByRole('menuitem', { name: '能力中心' }).click()
    await page.getByRole('menuitem', { name: '专业助手' }).click()

    await expect(page).toHaveURL(/\/agents$/)
    await expect(page.locator('.el-breadcrumb')).toContainText('能力协作')
    await expect(page.locator('.el-breadcrumb')).toContainText('专业助手')
    await expect(
      page.locator('.el-menu-item.is-active'),
    ).toContainText('专业助手')
  })

  test('未知路径显示站内 404 并可返回概览', async ({ page }) => {
    await login(page)
    await page.goto('/this-route-does-not-exist')

    await expect(page).toHaveTitle('页面不存在 · 阗策 · 科技小院')
    await expect(page.getByText('页面不存在', { exact: true }).first()).toBeVisible()
    await page.getByRole('button', { name: '返回概览' }).click()
    await expect(page).toHaveURL(/\/$/)
  })

  test('全部业务页可加载且无运行时异常', async ({ page }) => {
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page)
    const routes = [
      '/chat',
      '/qa',
      '/knowledge',
      '/tasks',
      '/review',
      '/agents',
      '/teams',
      '/market',
      '/agents/factory',
      '/devices',
      '/policies',
      '/evolution',
      '/quota',
      '/members',
      '/settings',
      '/bots',
    ]
    for (const path of routes) {
      await page.goto(path)
      await expect(page.locator('#main-content'), path).toBeVisible()
      await expect(page).toHaveURL(new RegExp(`${path.replace('/', '\\/')}$`))
      await expect(page.getByText('加载失败', { exact: true }), path).toHaveCount(0)
      await expect(page.getByText('页面不存在', { exact: true }), path).toHaveCount(0)
    }
    expect(errors).toEqual([])
  })
})

test.describe('成员邀请闭环', () => {
  test('注册空间 → 生成邀请链接 → 新成员加入 → 链接不可复用', async ({
    page,
    browser,
  }) => {
    const suffix = Date.now().toString(36)
    const tenantName = `E2E 空间 ${suffix}`
    const adminUser = `e2e_${suffix}`
    const password = 'e2ePass123'

    await page.goto('/signup')
    await page.getByPlaceholder('如：阳光生态农场').fill(tenantName)
    await page.getByPlaceholder('3-32 位字母、数字或下划线').fill(adminUser)
    await page.getByPlaceholder('至少 6 位').fill(password)
    await page.getByPlaceholder('请再次输入密码').fill(password)
    await page.getByRole('button', { name: '注册并进入' }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator('.el-breadcrumb')).toContainText(tenantName)

    await page.goto('/members')
    await expect(page.locator('.el-table')).toContainText(adminUser)

    await page.getByRole('tab', { name: '邀请链接' }).click()
    await page
      .locator('.page-actions')
      .getByRole('button', { name: '生成邀请链接' })
      .click()
    const dialog = page.getByRole('dialog', { name: '生成邀请链接' })
    await dialog.getByRole('button', { name: '生成链接' }).click()

    const linkInput = dialog.locator('.invite-link-box input')
    await expect(linkInput).toHaveValue(/\/invite\//)
    const inviteUrl = await linkInput.inputValue()
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()

    const invitee = await browser.newContext()
    const inviteePage = await invitee.newPage()
    await inviteePage.goto(inviteUrl)
    await expect(inviteePage.locator('.el-alert')).toContainText(tenantName)
    await inviteePage
      .getByPlaceholder('3-32 位字母、数字或下划线')
      .fill(`join_${suffix}`)
    await inviteePage.getByPlaceholder('至少 8 位').fill('joinPass123')
    await inviteePage.getByPlaceholder('再次输入密码').fill('joinPass123')
    await inviteePage.getByRole('button', { name: '接受邀请并进入' }).click()

    await expect(inviteePage).toHaveURL(/\/$/)
    await expect(inviteePage.locator('.el-breadcrumb')).toContainText(tenantName)
    await invitee.close()

    const reused = await browser.newContext()
    const reusedPage = await reused.newPage()
    await reusedPage.goto(inviteUrl)
    await expect(reusedPage.getByText('邀请链接不可用')).toBeVisible()
    await reused.close()
  })
})
