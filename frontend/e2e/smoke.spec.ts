import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('认证与导航', () => {
  test('错误密码给出中文提示且停留在登录页', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[autocomplete="username"]').fill('yard_admin')
    await page.locator('input[autocomplete="current-password"]').fill('wrong-password')
    await page.getByRole('button', { name: '登录', exact: true }).click()

    await expect(page.locator('.el-alert')).toContainText('用户名或密码错误')
    await expect(page).toHaveURL(/\/login/)
  })

  test('登录后导航按分组收敛，二级菜单可跳转', async ({ page }) => {
    await login(page)
    await expect(page).toHaveTitle(/^概览 · /)

    const topLevel = page.locator(
      '#main-nav > .el-menu > .el-menu-item-group > ul > li',
    )
    // 默认布局按「工作台 / 能力协作 / 知识数据 / 管理与统计」四组收敛；
    // 一级项随功能增减会变，这里只兜住下限并核对分组与关键入口都在。
    await expect(topLevel.first()).toBeVisible()
    expect(await topLevel.count()).toBeGreaterThanOrEqual(14)
    for (const group of ['工作台', '能力协作', '知识数据', '管理与统计']) {
      await expect(page.locator('#main-nav').getByText(group, { exact: true })).toBeVisible()
    }
    for (const entry of ['概览', '智能对话', '知识库', '用量额度']) {
      await expect(page.getByRole('menuitem', { name: entry, exact: true })).toBeVisible()
    }

    // 自我升级（/evolution）必须是一级可见入口，不能只藏在折叠菜单里
    await expect(page.getByRole('menuitem', { name: '自我升级' })).toBeVisible()

    await page.getByRole('menuitem', { name: '能力中心' }).click()
    await page.getByRole('menuitem', { name: '专业助手' }).click()

    await expect(page).toHaveURL(/\/agents$/)
    await expect(page.locator('.el-breadcrumb')).toContainText('能力协作')
    await expect(page.locator('.el-breadcrumb')).toContainText('专业助手')
    await expect(
      page.locator('.el-menu-item.is-active'),
    ).toContainText('专业助手')
  })

  test('「我的智能体」可从左侧菜单进入且不弹网络错误', async ({ page }) => {
    await login(page)
    await page.getByRole('menuitem', { name: '能力中心' }).click()
    await page.getByRole('menuitem', { name: '我的智能体' }).click()

    await expect(page).toHaveURL(/\/agents\/custom$/)
    await expect(page.getByRole('heading', { name: '我的智能体' })).toBeVisible()
    // 接口失败时统一错误 toast 会出现；页面首屏加载不得报「网络连接失败」
    await expect(page.locator('.el-message--error')).toHaveCount(0)
  })

  test('未知路径显示站内 404 并可返回概览', async ({ page }) => {
    await login(page)
    await page.goto('/this-route-does-not-exist')

    await expect(page).toHaveTitle(/^页面不存在 · /)
    await expect(page.getByText('页面不存在', { exact: true }).first()).toBeVisible()
    await page.getByRole('button', { name: '返回概览' }).click()
    await expect(page).toHaveURL(/\/$/)
  })

  test('旧问答入口重定向到对话并进入知识问答模式', async ({ page }) => {
    await login(page)

    await page.goto('/qa?q=水稻')
    await expect(page).toHaveURL(/\/chat\?.*qa=1/)

    const qaButton = page.getByRole('button', { name: '知识问答', exact: true })
    await expect(qaButton).toHaveCount(1)
    await expect(qaButton).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('button', { name: '通用对话', exact: true })).toBeVisible()
    await expect(page.getByRole('textbox', { name: '对话输入' })).toBeVisible()
    // 问答模式下点名/班组入口收敛，避免出现两套问答
    await expect(page.locator('.member-bar')).toHaveCount(0)
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
      '/knowledge',
      '/tasks',
      '/review',
      '/agents',
      '/agents/custom',
      '/teams',
      '/market',
      '/agents/factory',
      '/devices',
      '/policies',
      '/evolution',
      '/quota',
      '/library',
      '/members',
      '/settings',
      '/bots',
      '/profile',
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
  test('注册小院 → 生成邀请链接 → 新成员加入 → 链接不可复用', async ({
    page,
    browser,
  }) => {
    const suffix = Date.now().toString(36)
    const yardName = `E2E 小院 ${suffix}`
    const adminUser = `e2e_${suffix}`
    const password = 'e2ePass123'

    await page.goto('/signup')
    await page.getByPlaceholder('如：阳光生态农场').fill(yardName)
    await page.getByPlaceholder('3-32 位字母、数字或下划线').fill(adminUser)
    await page.getByPlaceholder('至少 6 位').fill(password)
    await page.getByPlaceholder('请再次输入密码').fill(password)
    await page.getByRole('button', { name: '注册并进入' }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator('.el-breadcrumb')).toContainText(yardName)

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
    await expect(inviteePage.locator('.el-alert')).toContainText(yardName)
    await inviteePage
      .getByPlaceholder('3-32 位字母、数字或下划线')
      .fill(`join_${suffix}`)
    await inviteePage.getByPlaceholder('至少 8 位').fill('joinPass123')
    await inviteePage.getByPlaceholder('再次输入密码').fill('joinPass123')
    await inviteePage.getByRole('button', { name: '接受邀请并进入' }).click()

    await expect(inviteePage).toHaveURL(/\/$/)
    await expect(inviteePage.locator('.el-breadcrumb')).toContainText(yardName)
    await invitee.close()

    const reused = await browser.newContext()
    const reusedPage = await reused.newPage()
    await reusedPage.goto(inviteUrl)
    await expect(reusedPage.getByText('邀请链接不可用')).toBeVisible()
    await reused.close()
  })
})

test.describe('负责人驾驶舱与插件库', () => {
  test('负责人可看到驾驶舱与插件/技能库，管理员不可进驾驶舱', async ({ page }) => {
    const errors: string[] = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    page.on('pageerror', err => errors.push(err.message))

    await login(page, 'admin', 'admin123')
    await page.goto('/owner')
    await expect(page.getByRole('heading', { name: '小院负责人驾驶舱' })).toBeVisible()
    await expect(page.getByText('403 无权限访问')).toHaveCount(0)

    await page.goto('/library')
    await expect(page.getByRole('heading', { name: '插件与技能库' })).toBeVisible()
    // 内置农业技能离线可检索（不依赖外网）
    await expect(page.locator('.lib-card').first()).toBeVisible({ timeout: 15_000 })

    await page.goto('/profile')
    await expect(page.getByRole('heading', { name: '个人资料' })).toBeVisible()

    expect(errors).toEqual([])
  })

  test('小院管理员访问驾驶舱被拦截', async ({ page }) => {
    await login(page)
    await page.goto('/owner')
    // 路由守卫按角色拦截：停留在无权限页或回退到概览
    await expect(page.getByRole('heading', { name: '小院负责人驾驶舱' })).toHaveCount(0)
  })
})
