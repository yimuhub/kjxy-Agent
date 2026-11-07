import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.51 指挥官伙伴重做：
 * a. 默认形象是戴草帽的农民小人（有草帽/双臂/汗珠分层，忙碌时擦汗）；
 * b. 面板只有「指挥官」一个身份，不出现普通/指挥官切换；
 * c. 面板可整体拖走、可拖角改大小，位置与尺寸都记住；
 * d. 聊天记录挂在一条专属会话上，刷新后仍在继续。
 */

test.describe('指挥官伙伴（v3.51）', () => {
  test('默认是农民小人、只有指挥官、可拖可缩放且历史续接', async ({ page }) => {
    await login(page)

    const companion = page.locator('[data-companion]')
    await expect(companion).toBeVisible()
    // 拟人化小人：草帽 + 双臂 + 汗珠都在，而不是圆球加 emoji
    const figure = companion.locator('.fig')
    await expect(figure).toBeVisible()
    await expect(figure.locator('.fig-hat')).toBeVisible()
    await expect(figure.locator('.fig-arm-wipe')).toBeVisible()
    await expect(figure.locator('.fig-sweat')).toBeVisible()

    // 头像拖到左上，位置记住
    const handle = page.locator('[data-companion-handle]')
    const box = await handle.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x - 260, box!.y - 180, { steps: 6 })
    await page.mouse.up()
    const anchor = await companion.evaluate(el => ({
      left: (el as HTMLElement).style.left,
      top: (el as HTMLElement).style.top,
    }))
    expect(Number.parseInt(anchor.left, 10)).toBeLessThan(box!.x)

    // 展开面板：只有指挥官，没有模式切换控件
    await handle.click()
    const panel = page.locator('[data-companion-panel]')
    await expect(panel).toBeVisible()
    await expect(panel.locator('.exec-seg')).toHaveCount(0)
    await expect(panel.getByText('普通', { exact: true })).toHaveCount(0)
    const composer = panel.locator('[data-panel="companion"] textarea')
    await expect(composer).toBeVisible()

    // 拖右下角改大小
    const before = await panel.boundingBox()
    expect(before).not.toBeNull()
    const corner = page.locator('[data-companion-resize="se"]')
    const cornerBox = await corner.boundingBox()
    expect(cornerBox).not.toBeNull()
    await page.mouse.move(cornerBox!.x + cornerBox!.width / 2, cornerBox!.y + cornerBox!.height / 2)
    await page.mouse.down()
    await page.mouse.move(cornerBox!.x - 120, cornerBox!.y - 90, { steps: 6 })
    await page.mouse.up()
    const afterResize = await panel.boundingBox()
    expect(afterResize!.width).toBeLessThan(before!.width)
    expect(afterResize!.height).toBeLessThan(before!.height)

    // 说一句，历史落进专属会话
    await composer.fill('记一笔：今天下午给东一号地浇水')
    await composer.press('Enter')
    await expect(panel.locator('.msg-row').first()).toBeVisible()
    const anchorSession = await page.evaluate(() => localStorage.getItem('ky_companion_session'))
    expect(anchorSession).toBeTruthy()
    const storedRect = await page.evaluate(() => localStorage.getItem('ky_pet_panel_rect'))
    expect(storedRect).toBeTruthy()

    // 刷新后续接同一条会话：之前说的话还在
    await page.reload()
    await expect(companion).toBeVisible()
    await handle.click()
    await expect(panel.locator('[data-panel="companion"] textarea')).toBeVisible()
    // 助理回复会引用原话，按 user 行断言避免 strict mode 命中两行
    await expect(
      panel
        .locator('.msg-row.user', { hasText: '记一笔：今天下午给东一号地浇水' })
        .last(),
    ).toBeVisible()
    // 面板回到拖过的大小与位置
    const afterReload = await panel.evaluate(el => ({
      left: (el as HTMLElement).style.left,
      top: (el as HTMLElement).style.top,
      width: (el as HTMLElement).style.width,
    }))
    expect(JSON.parse(storedRect!).width).toBe(Number.parseFloat(afterReload.width))
    expect(afterReload.left).toBeTruthy()
  })

  test('桌宠页默认形象是田伯，切回小芽仍是圆滚滚', async ({ page }) => {
    await page.goto('/pet')
    await expect(page.locator('.pet-name')).toContainText('田伯')
    await expect(page.locator('.pet-figure')).toBeVisible()
    await expect(page.locator('.pet-svg')).toHaveCount(0)

    await page.locator('.pet-page').click({ button: 'right', position: { x: 110, y: 220 } })
    const panel = page.locator('.skin-panel')
    await expect(panel.locator('.skin-chip')).toHaveCount(8)
    await panel.locator('.skin-chip', { hasText: '小芽' }).click()
    await expect(page.locator('.pet-name')).toContainText('小芽')
    await expect(page.locator('.pet-svg')).toBeVisible()
    await expect(page.locator('.pet-figure')).toHaveCount(0)
  })

  test('设置页可见指挥官的技能与工具挂载，并能增删保存', async ({ page }) => {
    await login(page)
    await page.goto('/settings')
    await page.locator('.el-tabs__item', { hasText: '指挥官' }).first().click()
    const card = page.locator('.settings-commander')
    await expect(card).toBeVisible()
    await expect(card.getByText('技能与工具', { exact: true })).toBeVisible()

    // 一键全量挂载：技能与工具都变成 N/N
    const attached = page.waitForResponse(
      res => res.url().includes('/commander/identity/attach-all') && res.status() === 200,
    )
    await card.getByRole('button', { name: '全部挂上' }).click()
    await attached
    const skillText = await card.locator('.identity-chip--skill').innerText()
    const [bound, total] = (skillText.match(/(\d+)\s*\/\s*(\d+)/) ?? []).slice(1).map(Number)
    expect(total).toBeGreaterThan(0)
    expect(bound).toBe(total)

    // 联网搜索与自建助手都挂在指挥官身上
    const webSearch = card.locator('.tool-item', { hasText: '联网搜索' }).first()
    await expect(webSearch.locator('input')).toBeChecked()
    const generate = card.locator('.tool-item', { hasText: '生成智能体' }).first()
    await expect(generate.locator('input')).toBeChecked()

    // 摘掉一个工具 → 保存 → 后端回包并落到清单上（点 label：原生 input 被 Element Plus 隐藏）
    await generate.locator('.el-checkbox__label').click()
    const saveButton = card.getByRole('button', { name: '保存技能与工具' })
    await expect(saveButton).toBeEnabled()
    const saved = page.waitForResponse(
      res => res.url().endsWith('/commander/identity') && res.status() === 200,
    )
    await saveButton.click()
    await saved
    await expect(
      card.locator('.tool-item', { hasText: '生成智能体' }).first().locator('input'),
    ).not.toBeChecked()
    await expect(saveButton).toBeDisabled()
  })

  test('窄屏只收起气泡、缩到最小宽度顶栏也不折行', async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 900 })
    await login(page)
    // 分屏/手机宽度下指挥官仍然在场，只是不再弹任务气泡挡输入区
    await expect(page.locator('[data-companion-handle]')).toBeVisible()
    await expect(page.locator('[data-companion-bubble]')).toHaveCount(0)

    await page.evaluate(() => localStorage.removeItem('ky_pet_panel_rect'))
    await page.locator('[data-companion-handle]').click()
    const panel = page.locator('[data-companion-panel]')
    await expect(panel).toBeVisible()

    // 往小里拖到触底：名字、徽章、状态必须还在同一条基线上
    const grip = page.locator('[data-companion-resize="se"]')
    const box = await grip.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x - 600, box!.y - 700, { steps: 8 })
    await page.mouse.up()
    await expect
      .poll(async () => {
        const rect = await panel.evaluate(el => {
          const r = (el as HTMLElement).getBoundingClientRect()
          return Math.round(r.width)
        })
        return rect
      })
      .toBeLessThanOrEqual(340)
    const tops = await page.locator('.panel-id > *').evaluateAll(nodes =>
      nodes.map(node => Math.round((node as HTMLElement).getBoundingClientRect().top)),
    )
    expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(4)
  })
})
