import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { login, sessionTotal } from './helpers'

/**
 * v3.15 会话列表管理与对话操作（三点菜单 / 进行中-已归档筛选 / 批量 / 分支 / 引用 / 操作权限档位）。
 *
 * 前三个用例不依赖模型回答（空会话、标记位、权限档位）；分支那条要跑一轮真实回答，
 * 单独放宽超时。所有测试数据统一以 E2E- 前缀命名，每个用例进来先清空上一次可能
 * 残留的同名会话，再动手——否则按标题定位会撞出多个元素。
 */

function panel(page: Page, panelId = 'main'): Locator {
  return page.locator(`.chat-panel[data-panel="${panelId}"]`)
}

function answered(target: Locator): Locator {
  return target.locator('.msg-row.assistant .bubble:not(.live-steps):not(.typing)')
}

async function sendIn(target: Locator, text: string) {
  const box = target.locator('textarea')
  await box.click()
  await box.fill(text)
  await box.press('Enter')
}

/** 列表里标题完全等于 title 的那一行 */
function rowOf(page: Page, title: string): Locator {
  return page.locator('.session-item').filter({ has: page.getByText(title, { exact: true }) })
}

/**
 * 打开某一行的三点菜单，返回这份菜单的定位器。
 *
 * 菜单弹出层带会话 id 的专属 class（且 persistent=false，收起即从 DOM 移除），
 * 所以按名字找菜单项只会命中这一行自己那份菜单，不会撞到别行的陈旧弹层。
 *
 * 会话 id 必须从「刚点开的那个按钮」上读：列表随时会重排（新会话置顶、归档后移位），
 * 先读 id 再回头找菜单，中间换行就会张冠李戴——按按钮节点取值才是一一对应。
 */
async function openMenu(page: Page, row: Locator): Promise<{ sid: string; menu: Locator }> {
  const handle = await row.locator('.s-more').elementHandle()
  if (!handle) throw new Error('找不到会话菜单按钮')
  await handle.click()
  const sid = await handle.evaluate(
    el => el.closest('.session-item')?.getAttribute('data-session-id') ?? '',
  )
  if (!sid) throw new Error('会话行缺少 data-session-id')
  const menu = page.locator(`.s-menu-${sid}`)
  await expect(menu).toBeVisible()
  return { sid, menu }
}

async function menuCommand(page: Page, row: Locator, item: string) {
  const { menu } = await openMenu(page, row)
  await menu.getByRole('menuitem', { name: item, exact: true }).click()
}

async function confirmDelete(page: Page) {
  await page.locator('.el-message-box').getByRole('button', { name: '删除', exact: true }).click()
}

/**
 * 切到「进行中 / 已归档」视图。
 *
 * 视图切换会回读整张会话列表，等这条请求落地再返回：否则下一步去读列表时
 * 拿到的还是上一个视图的旧行（重排中途的 DOM 甚至只剩一行），定位就会指错。
 * 已经在这个视图时直接返回——不会发出请求，等它就是空等。
 */
async function switchFilter(page: Page, label: '进行中' | '已归档') {
  const current = (await page.locator('.s-filter .el-segmented__item.is-selected').innerText()).trim()
  if (current === label) return
  const listed = page.waitForResponse(
    r =>
      r.request().method() === 'GET' &&
      /\/chat\/sessions\?/.test(r.url()) &&
      r.url().includes(`archived=${label === '已归档'}`) &&
      r.ok(),
  )
  await page.locator('.s-filter .el-segmented__item', { hasText: label }).click()
  await listed
}

/**
 * 清掉所有 E2E- 开头的残留会话（上一轮失败留下的也算）。
 *
 * 直接走接口删，不靠界面点：会话列表是窗口化渲染的（一次只渲染前 60 行），
 * 界面里看不见的残留会话就永远删不掉——上一轮就是这么把残留带进下一轮的。
 */
async function purgeE2E(page: Page) {
  const token = await page.evaluate(() => localStorage.getItem('ky_token') ?? '')
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const ids: string[] = []
  for (const archived of ['false', 'true']) {
    const res = await page.request.get(`/api/v1/chat/sessions?archived=${archived}`, { headers })
    if (!res.ok()) continue
    const body = await res.json()
    for (const s of body?.data?.items ?? []) {
      if (typeof s.title === 'string' && s.title.startsWith('E2E-')) ids.push(s.id)
    }
  }
  if (ids.length) {
    await page.request.post('/api/v1/chat/sessions/batch', {
      headers,
      data: { session_ids: ids, action: 'delete' },
    })
  }
  // 重载一次：让界面回到「干净列表」，后续定位不再受删过的行影响
  await page.reload()
  await expect(page.locator('.session-item').first()).toBeVisible()
}

/** 新建一条空会话并按 title 命名，返回它的行 */
async function createSession(page: Page, title: string) {
  // 新会话的 id 取自建会话接口的回包，不从列表 DOM 里猜：
  // 列表随时会重排（窗口化渲染 + 新会话置顶），「第一个没见过的 id」很可能是
  // 刚冒出来的旧会话——猜错就会把重命名打到别的会话上。
  const created = page.waitForResponse(
    r => r.request().method() === 'POST' && /\/chat\/sessions(\?|$)/.test(r.url()) && r.ok(),
  )
  await page.locator('.new-btn').click()
  const body = await (await created).json()
  const newId = body?.data?.id as string | undefined
  if (!newId) throw new Error('建会话回包里没有 id')
  const fresh = page.locator(`.session-item[data-session-id="${newId}"]`)
  await expect(fresh).toBeVisible()
  await menuCommand(page, fresh, '重命名')
  const box = page.locator('.el-message-box')
  await box.locator('input').fill(title)
  await box.getByRole('button', { name: '保存', exact: true }).click()
  const row = rowOf(page, title)
  await expect(row).toBeVisible()
  return row
}

test.describe('会话列表管理', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await page.goto('/chat')
    await expect(page.locator('.session-item').first()).toBeVisible()
    await purgeE2E(page)
  })

  test.afterEach(async ({ page }) => {
    await purgeE2E(page).catch(() => undefined)
  })

  test('三点菜单：置顶排最前、未读有标记、归档进出两个视图', async ({ page }) => {
    test.setTimeout(300_000)
    const a = await createSession(page, 'E2E-甲-置顶归档')
    const b = await createSession(page, 'E2E-乙-未读')

    // 置顶：恒排在最前，并出现图钉
    await menuCommand(page, a, '置顶')
    await expect(a.locator('.s-pin')).toBeVisible()
    await expect(page.locator('.session-item').first()).toContainText('E2E-甲-置顶归档')

    // 标记未读：.is-unread 驱动标题加粗与时间后的圆点
    await menuCommand(page, b, '标记未读')
    await expect(b).toHaveClass(/is-unread/)

    // 归档：从「进行中」消失，切到「已归档」才看得到
    await menuCommand(page, a, '归档')
    await expect(rowOf(page, 'E2E-甲-置顶归档')).toHaveCount(0)
    await switchFilter(page, '已归档')
    await expect(rowOf(page, 'E2E-甲-置顶归档')).toBeVisible()

    // 移回进行中
    await menuCommand(page, rowOf(page, 'E2E-甲-置顶归档'), '移回进行中')
    await switchFilter(page, '进行中')
    await expect(rowOf(page, 'E2E-甲-置顶归档')).toBeVisible()

    // 取消置顶：图钉消失，排序回到「最近更新在前」——刚才这次点击本身就是最近更新，
    // 所以它排在最前是对的，这里只断言置顶标记与两条都还在
    await menuCommand(page, rowOf(page, 'E2E-甲-置顶归档'), '取消置顶')
    await expect(rowOf(page, 'E2E-甲-置顶归档').locator('.s-pin')).toHaveCount(0)
    await expect(rowOf(page, 'E2E-乙-未读')).toBeVisible()
    await expect(rowOf(page, 'E2E-甲-置顶归档')).toBeVisible()
  })

  test('批量模式：勾选两条一次归档，归档视图里批量删除', async ({ page }) => {
    test.setTimeout(300_000)
    const one = await createSession(page, 'E2E-批量-1')
    const two = await createSession(page, 'E2E-批量-2')

    await page.locator('.s-batch-btn').click()
    await expect(page.locator('.s-batchbar')).toBeVisible()
    await expect(one.locator('.s-check')).toBeVisible()

    // 点 label（Element Plus 的原生 input 是隐藏的）
    await one.locator('.s-check').click()
    await two.locator('.s-check').click()
    await expect(page.locator('.s-batch-count')).toHaveText('已选 2')

    await page.locator('.s-batch-acts').getByText('归档', { exact: true }).click()
    await expect(rowOf(page, 'E2E-批量-1')).toHaveCount(0)
    await expect(rowOf(page, 'E2E-批量-2')).toHaveCount(0)

    // 归档视图里批量删除
    await switchFilter(page, '已归档')
    await expect(rowOf(page, 'E2E-批量-1')).toBeVisible()
    await page.locator('.s-batch-btn').click()
    await rowOf(page, 'E2E-批量-1').locator('.s-check').click()
    await rowOf(page, 'E2E-批量-2').locator('.s-check').click()
    await page.locator('.s-batch-del').click()
    await confirmDelete(page)
    await expect(rowOf(page, 'E2E-批量-1')).toHaveCount(0)
    await expect(rowOf(page, 'E2E-批量-2')).toHaveCount(0)
  })

  test('输入框处的操作权限档位：默认每次询问，管理员可切到自动放行与完全访问', async ({
    page,
  }) => {
    test.setTimeout(300_000)
    await page.locator('.new-btn').click()
    await expect(panel(page).locator('.chat-empty')).toBeVisible()

    const pill = page.locator('.ap-pill')
    await expect(pill).toBeVisible()
    // 默认档：每次询问（与历史行为一致，零回归）
    await expect(pill.locator('.ap-text')).toHaveText('每次询问')

    const pickMode = async (item: RegExp) => {
      await pill.click()
      const menu = page.locator('.ap-popper')
      await expect(menu).toBeVisible()
      await menu.getByRole('menuitem', { name: item }).click()
    }

    await pickMode(/会话内自动放行/)
    // 显示的是服务端回包的生效值，不是请求值
    await expect(pill.locator('.ap-text')).toHaveText('会话内自动放行')

    await pickMode(/完全访问/)
    await expect(pill.locator('.ap-text')).toHaveText('完全访问')

    await pickMode(/每次询问/)
    await expect(pill.locator('.ap-text')).toHaveText('每次询问')
  })

  test('长列表窗口化：首屏只渲染一页，滚到底再放下一页', async ({ page }) => {
    test.setTimeout(300_000)
    const LIST_PAGE = 60
    const total = await sessionTotal(page)
    // 不足一页时窗口化无从观察，如实跳过，不硬凑一个永远成立的断言
    test.skip(total <= LIST_PAGE, `当前视图只有 ${total} 条会话，看不出窗口化`)

    const first = await page.locator('.session-item').count()
    expect(first).toBeLessThanOrEqual(LIST_PAGE)

    await page.locator('.s-sentinel').scrollIntoViewIfNeeded()
    await expect(page.locator('.s-sentinel')).toBeVisible()
    await expect
      .poll(() => page.locator('.session-item').count(), { timeout: 15_000 })
      .toBeGreaterThan(first)

    // 窗口只决定渲染多少行，模型总数不变：滚动不会凭空增减会话，
    // 而「全选」拿到的仍然是整个视图（含尚未渲染的那些行）
    expect(await sessionTotal(page)).toBe(total)
    await page.locator('.s-batch-btn').click()
    await page.locator('.s-batch-all').click()
    await expect(page.locator('.s-batch-count')).toHaveText(`已选 ${total}`)
  })
})

test.describe('回答的分支与引用', () => {
  test('引用把原文贴进输入框；分支另开一个只到这条回答为止的会话', async ({ page }) => {
    test.setTimeout(420_000)
    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await page.goto('/chat')
    await expect(page.locator('.session-item').first()).toBeVisible()
    await purgeE2E(page)

    const main = panel(page)
    const title = 'E2E-分支引用原会话'
    const row = await createSession(page, title)

    await sendIn(main, '用一句话说明水稻分蘖期管理的要点')
    await expect(answered(main).first()).toBeVisible({ timeout: 300_000 })
    await expect(main.locator('.send-btn')).not.toHaveClass(/stop/, { timeout: 300_000 })

    // 引用：原文以 Markdown 引用块落进输入框
    const lastAssistant = main.locator('.msg-row.assistant').last()
    await lastAssistant.getByRole('button', { name: /引用这条回答/ }).click()
    const draft = await main.locator('textarea').inputValue()
    expect(draft).toContain('引用这条回答')
    expect(draft).toContain('>')
    await main.locator('textarea').fill('')

    // 分支：新会话标题带「· 分支」，列表出现分支角标，历史只到这条回答为止
    await lastAssistant.getByRole('button', { name: /从这条回答分支/ }).click()
    await expect(page.locator('.t-title')).toHaveText(`${title} · 分支`)
    const branchRow = rowOf(page, `${title} · 分支`)
    await expect(branchRow).toBeVisible()
    await expect(branchRow.locator('.s-branch')).toBeVisible()
    await expect(main.locator('.msg-row')).toHaveCount(2)

    // 原会话不受影响：切回去仍然有内容
    await row.click()
    await expect(answered(main).first()).toBeVisible()

    await purgeE2E(page)
  })
})
