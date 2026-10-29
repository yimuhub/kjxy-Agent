import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.25.1 弹窗布局与操作栏回归：
 *
 * 1) 弹窗必须脱离页面卡片外框（append-to-body）。页面上带 transform/backdrop-filter
 *    的祖先会成为 position:fixed 的包含块，把遮罩层困在卡片里，弹窗被「框住」且
 *    尺寸被压扁 —— 这里断言遮罩直接挂在 BODY 下，且遮罩到 BODY 之间没有会截断
 *    fixed 定位的祖先。
 * 2) 全站弹窗从视口正中弹出：水平居中、完整落在视口内（内容比视口高时才允许
 *    顶部对齐）。右侧抽屉已全部改为居中弹窗，页面里不应再出现 el-drawer。
 * 3) 智能体操作栏（卡片视图与列表视图同款）：对话/发布(停用)/更多 同一行、
 *    不换行、不压字；「编辑/复制/删除」收进「更多」下拉，避免第三个按钮把
 *    操作栏挤到换行。
 */

type DialogMetrics = {
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
  viewportWidth: number
  viewportHeight: number
  /** 遮罩层的父节点标签名：必须是 BODY */
  overlayParent: string | null
  /** 遮罩到 BODY 之间是否存在会截断 fixed 定位的祖先（transform/filter/backdrop-filter） */
  trappedBy: string | null
}

async function measureDialog(page: Page, dialog: Locator): Promise<DialogMetrics> {
  const handle = await dialog.first().elementHandle()
  if (!handle) throw new Error('找不到弹窗元素')
  const metrics = await handle.evaluate(el => {
    const rect = el.getBoundingClientRect()
    const overlay = el.closest('.el-overlay')
    let trappedBy: string | null = null
    // 从遮罩的父节点往上找：祖先里有 transform/filter/backdrop-filter 时，
    // 它会成为 fixed 定位的包含块，遮罩就被困在那个盒子里（弹窗被外框框住）。
    let node: HTMLElement | null = overlay?.parentElement ?? el.parentElement
    while (node && node !== document.body) {
      const cs = window.getComputedStyle(node)
      const traps =
        cs.transform !== 'none' ||
        cs.filter !== 'none' ||
        (cs.backdropFilter && cs.backdropFilter !== 'none') ||
        (cs.webkitBackdropFilter && cs.webkitBackdropFilter !== 'none')
      if (traps) {
        trappedBy = `${node.tagName.toLowerCase()}.${String(node.className || '').trim()}`
        break
      }
      node = node.parentElement
    }
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      overlayParent: overlay?.parentElement?.tagName ?? null,
      trappedBy,
    }
  })
  await handle.dispose()
  return metrics as DialogMetrics
}

/**
 * 断言弹窗脱离外框、完整可见且水平居中（垂直方向内容超高时允许顶对齐）。
 *
 * 弹窗打开有淡入动画，边框与外边距在动画期间是过渡值（量到 top 为负的中间态），
 * 所以这里轮询到布局稳定后再断言，而不是拿动画中的某一帧当结论。
 */
async function expectCenteredDialog(page: Page, dialog: Locator): Promise<DialogMetrics> {
  await expect
    .poll(
      async () => {
        const m = await measureDialog(page, dialog)
        return {
          overlayParent: m.overlayParent,
          trappedBy: m.trappedBy,
          inside:
            m.left >= 0 &&
            m.top >= 0 &&
            m.right <= m.viewportWidth + 1 &&
            m.bottom <= m.viewportHeight + 1,
          hCentered: Math.abs(m.left + m.width / 2 - m.viewportWidth / 2) <= 2,
          vCentered:
            m.height + 32 > m.viewportHeight ||
            Math.abs(m.top + m.height / 2 - m.viewportHeight / 2) <= 2,
        }
      },
      { timeout: 15_000, message: '弹窗应脱离外框并从视口正中弹出' },
    )
    .toEqual({
      overlayParent: 'BODY',
      trappedBy: null,
      inside: true,
      hCentered: true,
      vCentered: true,
    })
  return measureDialog(page, dialog)
}

/** 断言操作栏里的按钮排在一行、不换行不溢出。 */
async function expectSingleLineActions(actions: Locator): Promise<void> {
  const tops = await actions.locator('button').evaluateAll(els =>
    els.map(el => Math.round(el.getBoundingClientRect().top)),
  )
  expect(tops.length).toBeGreaterThanOrEqual(3)
  // 同一行 → 所有按钮顶端一致（换行会出现第二个 top 值）
  expect(new Set(tops).size).toBe(1)
  // 按钮总宽不撑破操作栏（否则最后一项被挤到下一行）
  const overflow = await actions.evaluate(el => ({
    boxWidth: el.getBoundingClientRect().width,
    scrollWidth: el.scrollWidth,
  }))
  expect(overflow.scrollWidth).toBeLessThanOrEqual(Math.ceil(overflow.boxWidth) + 1)
}

async function apiToken(request: APIRequestContext): Promise<string> {
  const resp = await request.post('/api/v1/auth/login', {
    data: { username: 'yard_admin', password: 'yard123' },
  })
  expect(resp.ok()).toBeTruthy()
  return (await resp.json()).data.access_token as string
}

test.describe('弹窗布局与操作栏（v3.25.1）', () => {
  // 首次进入 /model-center 与 /settings 需要现编前端分块，留出冷启动余量
  test.describe.configure({ timeout: 180_000 })

  test('模型中心：新增供应商弹窗脱离外框、居中且不被压扁', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/model-center')

    await page.getByRole('tab', { name: '供应商' }).click()
    await page.getByRole('button', { name: '新增供应商' }).click()

    const dialog = page.getByRole('dialog', { name: '新增供应商' })
    await expect(dialog).toBeVisible()
    const m = await expectCenteredDialog(page, dialog)
    // 宽度没被外框压成窄条（弹窗声明 620px）
    expect(m.width).toBeGreaterThanOrEqual(560)
    // 表单字段真的渲染出来了，而不是被裁掉
    await expect(dialog.getByText('预置模板')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  })

  test('成员权限弹窗：居中、完整可见', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/members')

    const row = page.locator('.el-table tbody tr').filter({ hasText: 'operator' }).first()
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: '权限' }).click()

    const dialog = page.getByRole('dialog', { name: '成员权限与逐人授权' })
    await expect(dialog).toBeVisible()
    await expectCenteredDialog(page, dialog)
    // 逐人授权区域真的可见（曾经被外框裁掉一半）
    await expect(dialog.getByText('有效权限 = 角色默认权限')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  })

  test('可用工具详情：是居中弹窗，页面里不再有右侧抽屉', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=tools')

    const first = page.locator('.tool-link').first()
    await expect(first).toBeVisible()
    await first.click()

    const detail = page.locator('.el-dialog:visible').filter({ has: page.locator('.detail-tags') })
    await expect(detail).toBeVisible()
    await expectCenteredDialog(page, detail)
    await expect(detail.getByText('能做什么')).toBeVisible()

    // 全站不再有抽屉：右侧滑出的详情一律改成居中弹窗
    await expect(page.locator('.el-drawer')).toHaveCount(0)

    await page.keyboard.press('Escape')
    await expect(detail).toBeHidden()
  })

  test('智能体列表：操作栏不换行不压字，删除收进更多菜单', async ({ page, request }) => {
    const token = await apiToken(request)
    const code = `e2e3251_ui_${Date.now().toString(36).slice(-6)}`
    const name = `E2E 3251 界面助手 ${code}`
    const created = await request.post('/api/v1/agents/declarative', {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        agent_code: code,
        name,
        persona_prompt: '你是一个用于界面回归测试的助手。',
        task_prompt_template: '请回答关于 {{question}} 的问题。',
      },
    })
    expect(created.ok()).toBeTruthy()
    const agentId = (await created.json()).data.id as string

    try {
      // 列表视图：卡片视图下没有操作栏
      await page.addInitScript(() => {
        window.localStorage.setItem('ky_custom_agents_view', 'list')
      })
      await login(page)
      await page.goto('/agents/custom')

      const row = page.locator('.el-table tbody tr').filter({ hasText: code }).first()
      await expect(row).toBeVisible()

      const actions = row.locator('.da-actions')
      await expect(actions).toBeVisible()
      await expectSingleLineActions(actions)

      // 「对话」只出现一次；「删除」不再常驻在操作栏里
      await expect(actions.getByRole('button', { name: '对话', exact: true })).toHaveCount(1)
      await expect(actions.getByRole('button', { name: '删除' })).toHaveCount(0)
      await expect(actions.getByRole('button', { name: '更多操作' })).toHaveCount(1)

      // 「更多」里能拿到编辑/复制/删除
      await actions.getByRole('button', { name: '更多操作' }).click()
      const items = page.locator('.el-dropdown-menu__item:visible')
      await expect(items).toHaveCount(3)
      await expect(items.filter({ hasText: '编辑' })).toBeVisible()
      await expect(items.filter({ hasText: '复制' })).toBeVisible()
      await expect(items.filter({ hasText: '删除' })).toBeVisible()
    } finally {
      await request.delete(`/api/v1/agents/declarative/${agentId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  })

  test('智能体卡片：操作栏同样不换行，删除收进更多菜单', async ({ page, request }) => {
    const token = await apiToken(request)
    const code = `e2e3251_card_${Date.now().toString(36).slice(-6)}`
    const name = `E2E 3251 卡片助手 ${code}`
    const created = await request.post('/api/v1/agents/declarative', {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        agent_code: code,
        name,
        persona_prompt: '你是一个用于界面回归测试的助手。',
        task_prompt_template: '请回答关于 {{question}} 的问题。',
      },
    })
    expect(created.ok()).toBeTruthy()
    const agentId = (await created.json()).data.id as string

    try {
      await page.addInitScript(() => {
        window.localStorage.setItem('ky_custom_agents_view', 'card')
      })
      await login(page)
      await page.goto('/agents/custom')

      const card = page.locator('.da-card').filter({ hasText: code }).first()
      await expect(card).toBeVisible()

      const actions = card.locator('.da-actions')
      await expect(actions).toBeVisible()
      await expectSingleLineActions(actions)

      await expect(actions.getByRole('button', { name: '对话', exact: true })).toHaveCount(1)
      await expect(actions.getByRole('button', { name: '删除' })).toHaveCount(0)

      await actions.getByRole('button', { name: '更多操作' }).click()
      const items = page.locator('.el-dropdown-menu__item:visible')
      await expect(items).toHaveCount(3)
      await expect(items.filter({ hasText: '编辑' })).toBeVisible()
      await expect(items.filter({ hasText: '复制' })).toBeVisible()
      await expect(items.filter({ hasText: '删除' })).toBeVisible()
    } finally {
      await request.delete(`/api/v1/agents/declarative/${agentId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  })
})
