import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.50 页面内悬浮指挥官伙伴 + 菜单缓存补齐：
 * a. 伙伴在任意页面常驻，可拖到任意位置并记住，展开即可下指令；
 * b. 右键/按钮可换肤（含农民伯伯），偏好持久化；
 * c. 3.32 时代的自定义菜单缓存升级后会自动补回「指挥官工作台」。
 */

const SEEN_PREFIX = 'kjxy_menu_surfaced_v1_'

async function companionBox(page: import('@playwright/test').Page) {
  return page.locator('[data-companion]').boundingBox()
}

/** 菜单补齐标记由 loadFromServer 写入，需等它出现后才能拿到当前用户的布局键 */
async function waitLayoutKey(page: import('@playwright/test').Page): Promise<string> {
  let key = ''
  await expect
    .poll(
      async () => {
        key = await page.evaluate(prefix => {
          const marker = Object.keys(localStorage).find(item => item.startsWith(prefix))
          return marker ? marker.slice(prefix.length) : ''
        }, SEEN_PREFIX)
        return key
      },
      { timeout: 15000 },
    )
    .toBeTruthy()
  return key
}

/** 覆盖本地布局（模拟老版本缓存）；clearSeen 用于回到「从未补齐过」的状态 */
async function writeLocalLayout(
  page: import('@playwright/test').Page,
  layoutKey: string,
  keys: string[],
  clearSeen: boolean,
): Promise<void> {
  await page.evaluate(
    ({ target, items, wipeSeen }) => {
      localStorage.setItem(
        target,
        JSON.stringify([
          {
            id: 'grp-workbench',
            label: '工作台',
            items: items.map(key => ({
              key,
              title: key === 'chat' ? '智能对话' : key,
              iconName: 'ChatDotRound',
              path: `/${key}`,
            })),
          },
        ]),
      )
      if (wipeSeen) {
        Object.keys(localStorage)
          .filter(item => item.startsWith('kjxy_menu_surfaced_v1_'))
          .forEach(item => localStorage.removeItem(item))
      }
    },
    { target: layoutKey, items: keys, wipeSeen: clearSeen },
  )
}

async function readSeen(page: import('@playwright/test').Page, layoutKey: string): Promise<string[]> {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '[]') as string[], `${SEEN_PREFIX}${layoutKey}`)
}

test.describe('悬浮指挥官伙伴（v3.50）', () => {
  test('常驻、可拖拽、位置持久化并可下指令', async ({ page }) => {
    await login(page)

    const companion = page.locator('[data-companion]')
    await expect(companion).toBeVisible()
    await expect(page.locator('[data-companion-bubble]')).toBeVisible()

    // 换页不消失：任意界面随时可出来
    await page.goto('/farm')
    await expect(companion).toBeVisible()

    const before = await companionBox(page)
    expect(before).not.toBeNull()

    // 拖到左上一点，位置应改变并写入本地存储
    const handle = page.locator('[data-companion-handle]')
    const box = await handle.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x - 220, box!.y - 160, { steps: 6 })
    await page.mouse.up()

    const afterDrag = await companion.evaluate(el => ({
      left: (el as HTMLElement).style.left,
      top: (el as HTMLElement).style.top,
    }))
    expect(Number.parseInt(afterDrag.left, 10)).toBeLessThan(before!.x)
    const stored = await page.evaluate(() => localStorage.getItem('ky_pet_pos'))
    expect(stored).toBeTruthy()

    // 刷新后仍在拖到的位置
    await page.reload()
    await expect(companion).toBeVisible()
    const afterReload = await companion.evaluate(el => ({
      left: (el as HTMLElement).style.left,
      top: (el as HTMLElement).style.top,
    }))
    expect(afterReload).toEqual(afterDrag)

    // 点一下展开面板，直接对指挥官说一句目标
    await handle.click()
    const panel = page.locator('[data-companion-panel]')
    await expect(panel).toBeVisible()
    const composer = panel.locator('[data-panel="companion"] textarea')
    await expect(composer).toBeVisible()
    await composer.fill('帮我记一笔今天给东一号地浇水的农事')
    await composer.press('Enter')
    await expect(panel.locator('.msg-row').first()).toBeVisible()

    // 换肤：农民伯伯在皮肤循环里，选择会持久化
    const titleName = panel.locator('.panel-title strong')
    const skinBefore = await titleName.innerText()
    await panel.getByRole('button', { name: '换肤' }).click()
    const skinAfter = await titleName.innerText()
    expect(skinAfter).not.toEqual(skinBefore)
    expect(['小芽', '向日', '芋泥', '糯米', '小轨', '小火', '团子', '田伯']).toContain(skinAfter)
    expect(await page.evaluate(() => localStorage.getItem('ky_pet_skin'))).toBeTruthy()
  })

  test('设置页可关闭页面内悬浮伙伴', async ({ page }) => {
    await login(page)
    await expect(page.locator('[data-companion]')).toBeVisible()

    await page.goto('/settings?tab=desktop_pet')
    const overlaySwitch = page
      .locator('.el-form-item', { hasText: '页面内悬浮伙伴' })
      .locator('.el-switch')
    await overlaySwitch.click()
    await expect(page.locator('[data-companion]')).toHaveCount(0)
    expect(await page.evaluate(() => localStorage.getItem('ky_pet_overlay'))).toBe('0')

    await overlaySwitch.click()
    await expect(page.locator('[data-companion]')).toBeVisible()
  })

  test('老菜单缓存升级后自动补回指挥官工作台', async ({ page }) => {
    await login(page)

    // 布局键来自 loadFromServer 写下的补齐标记，需等它就绪后再伪造老缓存
    const layoutKey = await waitLayoutKey(page)

    // 模拟 3.32 时代的本地缓存：只有智能对话，且从未补齐过
    await writeLocalLayout(page, layoutKey, ['chat'], true)

    await page.reload()
    await expect(page.getByRole('menuitem', { name: /指挥官工作台/ })).toBeVisible()
    await expect
      .poll(() => readSeen(page, layoutKey), { timeout: 15000 })
      .toContain('commander')

    // 补齐过一次后，用户再手动隐藏就不再反复弹回
    await writeLocalLayout(page, layoutKey, ['chat'], false)
    await page.reload()
    await expect(page.getByRole('menuitem', { name: /指挥官工作台/ })).toHaveCount(0)
  })
})
