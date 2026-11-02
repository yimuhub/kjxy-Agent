import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.52 悬浮伙伴「能力」抽屉 + 农民小人动效：
 * a. 伙伴面板里就能选大模型、挂技能与插件（不必绕去系统设置），保存后刷新仍在；
 * b. 技能/工具清单在窄版面板里逐行排开，不再叠成一片；
 * c. 小人发呆时自己找活干：刨土、擦汗、扇帽子、点头轮着来，动作类会持续变化；
 * d. 小人放大后停在右下角会把对话页的发送钮整颗盖住，输入条要主动让位。
 */

test.describe('指挥官伙伴能力与动效（v3.52）', () => {
  test('伙伴面板内可选大模型并保存，刷新后仍是同一个模型', async ({ page }) => {
    await login(page)
    await page.locator('[data-companion-handle]').click()
    await expect(page.locator('[data-companion-panel]')).toBeVisible()

    await page.getByRole('button', { name: '能力' }).click()
    const abilities = page.locator('[data-companion-abilities]')
    await expect(abilities).toBeVisible()
    // 与系统设置同一份实现：模型 + 技能 + 工具三块都在
    await expect(abilities.getByText('大模型', { exact: true })).toBeVisible()
    await expect(abilities.getByText('技能与工具', { exact: true })).toBeVisible()

    const before = await abilities.locator('.abilities-note').first().innerText()

    await abilities.locator('.model-select .el-select__wrapper').click()
    const option = page.locator('.el-select-dropdown__item').first()
    await expect(option).toBeVisible()
    // 伙伴面板本体 z-index 2601：下拉必须开在面板之上，否则「看得见点不着」
    const optionBox = await option.boundingBox()
    expect(optionBox).not.toBeNull()
    await expect
      .poll(async () =>
        page.evaluate(
          ([x, y]) =>
            document
              .elementFromPoint(x, y)
              ?.closest('.el-select-dropdown') !== null,
          [optionBox!.x + optionBox!.width / 2, optionBox!.y + optionBox!.height / 2],
        ),
      )
      .toBe(true)
    const optionText = (await option.innerText()).trim()
    await option.click()

    const saveButton = abilities.getByRole('button', { name: '保存模型' })
    await expect(saveButton).toBeEnabled()
    const saved = page.waitForResponse(
      res => res.url().endsWith('/llm/bindings') && res.status() === 200,
    )
    await saveButton.click()
    expect((await saved).status()).toBe(200)

    // 提示语给的是「供应商 · 模型名」，不是内部 id
    await expect
      .poll(async () => abilities.locator('.abilities-note').first().innerText())
      .toContain(optionText.split(' · ')[1] ?? optionText)

    // 刷新后绑定还在（真的写到了 agent 层，而不是只改了前端状态）
    await page.reload()
    await page.locator('[data-companion-handle]').click()
    await page.getByRole('button', { name: '能力' }).click()
    const after = page.locator('[data-companion-abilities]')
    await expect(after.locator('.abilities-note').first()).toContainText('已单独指定')

    // 收尾：恢复原来的跟随默认，别把别的用例的模型选择带跑
    if (before.includes('跟随')) {
      const reset = after.getByRole('button', { name: '跟随默认' })
      const cleared = page.waitForResponse(
        res => res.url().endsWith('/llm/bindings') && res.status() === 200,
      )
      await reset.click()
      await cleared
    }
  })

  test('窄版技能清单逐行排开，不叠字', async ({ page }) => {
    await login(page)
    await page.evaluate(() =>
      localStorage.setItem(
        'ky_pet_panel_rect',
        JSON.stringify({ x: 620, y: 40, width: 640, height: 760 }),
      ),
    )
    await page.locator('[data-companion-handle]').click()
    await page.getByRole('button', { name: '能力' }).click()
    const abilities = page.locator('[data-companion-abilities]')
    await expect(abilities.locator('.tool-item').first()).toBeVisible()

    const tops = await abilities.locator('.tool-item').evaluateAll(nodes =>
      nodes.slice(0, 6).map(node => Math.round((node as HTMLElement).getBoundingClientRect().top)),
    )
    expect(tops.length).toBeGreaterThan(1)
    // 同一行的两列允许相等，但相邻行必须往下走
    for (let i = 1; i < tops.length; i += 1) {
      expect(tops[i]).toBeGreaterThanOrEqual(tops[i - 1])
    }
    expect(Math.max(...tops) - Math.min(...tops)).toBeGreaterThan(20)
  })

  test('发呆时小人自己换动作：刨土/擦汗/扇帽子/点头', async ({ page }) => {
    await page.goto('/pet')
    const figure = page.locator('.fig')
    await expect(figure).toBeVisible()
    // 放大后的画布：160×200 的 viewBox，比 v3.51 更大更清楚
    await expect(figure).toHaveAttribute('viewBox', '0 0 160 200')

    const acts = new Set<string>()
    await expect
      .poll(
        async () => {
          const cls = await figure.getAttribute('class')
          const matched = /act-(\w+)/.exec(cls ?? '')
          if (matched && matched[1] !== 'idle') acts.add(matched[1])
          return acts.size
        },
        { timeout: 20000, intervals: [400] },
      )
      .toBeGreaterThanOrEqual(3)
    expect([...acts]).toEqual(expect.arrayContaining(['dig', 'wipe', 'fan']))
  })

  test('小人停在右下角时输入条让位：发送钮点得到，挪开后恢复满宽', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    await page.locator('.new-btn').click()
    await expect(page.locator('.chat-empty')).toBeVisible()

    const send = page.locator('[data-panel="main"] [aria-label="发送"]')
    await expect(send).toBeVisible()
    // 伙伴浮层（z-index 2600）盖在页面之上：不让位时命中的是小人的 SVG，点下去只会打开面板
    const box = await send.boundingBox()
    expect(box).not.toBeNull()
    await expect
      .poll(async () =>
        page.evaluate(
          ([x, y]) => document.elementFromPoint(x, y)?.closest('.send-btn') !== null,
          [box!.x + box!.width / 2, box!.y + box!.height / 2],
        ),
      )
      .toBe(true)
    await expect(page.locator('[data-panel="main"] .composer-toolbar')).toHaveClass(/clear-dock/)

    // 让位只因「正压在角落」而存在：把小人拖到左上角，标记应立刻撤销
    await page.evaluate(() => localStorage.setItem('ky_pet_pos', JSON.stringify({ x: 60, y: 60 })))
    await page.reload()
    await expect(page.locator('[data-companion-handle]')).toBeVisible()
    await expect(page.locator('[data-panel="main"] .composer-toolbar')).not.toHaveClass(/clear-dock/)
  })
})
