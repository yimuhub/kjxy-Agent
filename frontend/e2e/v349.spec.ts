import { expect, test, type Page } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.49 桌面伙伴主动陪伴：
 * a. 时段/节气问候与场景提示可见；
 * b. 对话/指挥官事件可驱动状态、气泡与动画；
 * c. 静音只关闭任务提醒，偏好跨刷新保留；
 * d. 主窗路由变化会把当前场景同步给桌宠窗口。
 */

let eventSeq = 0

async function emitPet(page: Page, payload: Record<string, unknown>): Promise<void> {
  eventSeq += 1
  await page.evaluate(
    event => {
      const channel = new BroadcastChannel('ky_pet_copilot')
      channel.postMessage(event)
      channel.close()
    },
    { id: `e2e-${Date.now()}-${eventSeq}`, at: Date.now(), ...payload },
  )
}

test.describe('桌面伙伴主动陪伴（v3.49）', () => {
  test('问候、状态、场景与静音可用', async ({ page }) => {
    await page.goto('/pet')
    const pet = page.locator('.pet-page')
    await expect(pet).toBeVisible()

    await expect(page.locator('.pet-greeting')).toContainText(
      /早上好|上午好|中午好|下午好|晚上好|夜深了|快乐/,
    )
    await expect(page.locator('.pet-state')).toHaveText('发呆中')
    await expect(page.locator('.pet-bubble')).toContainText('今天想先做哪件事')

    await emitPet(page, { type: 'mood', mood: 'busy', text: '正在生成报告' })
    await expect(pet).toHaveClass(/mood-busy/)
    await expect(page.locator('.pet-state')).toHaveText('忙碌中')
    await expect(page.locator('.pet-bubble')).toContainText('正在生成报告')

    await emitPet(page, { type: 'mood', mood: 'worried', text: '预算到上限了' })
    await expect(page.locator('.pet-state')).toHaveText('有点担心')
    await expect(page.locator('.pet-bubble')).toContainText('预算到上限了')

    await emitPet(page, { type: 'scene', scene: 'farm' })
    await expect(page.locator('.pet-bubble')).toContainText(/地块|农事/)
    expect(await page.evaluate(() => localStorage.getItem('ky_pet_scene'))).toBe('farm')

    await pet.click({ button: 'right', position: { x: 110, y: 220 } })
    const panel = page.locator('.skin-panel')
    await expect(panel).toBeVisible()
    await panel.getByRole('button', { name: '静音提醒' }).click()
    expect(await page.evaluate(() => localStorage.getItem('ky_pet_mute'))).toBe('1')
    await expect(panel.getByRole('button', { name: '开启提醒' })).toBeVisible()

    await emitPet(page, { type: 'mood', mood: 'busy', text: '这条不该出现' })
    await expect(page.locator('.pet-state')).toHaveText('忙碌中')
    await expect(page.locator('.pet-bubble')).not.toContainText('这条不该出现')

    await page.reload()
    await expect(page.locator('.pet-state')).toHaveText('发呆中')
    await expect(page.locator('.pet-greeting')).toBeVisible()
    await expect(page.locator('.pet-bubble')).toContainText(/地块|农事/)

    await page.locator('.pet-page').click({ button: 'right', position: { x: 110, y: 220 } })
    await page.locator('.skin-panel').getByRole('button', { name: '开启提醒' }).click()
    expect(await page.evaluate(() => localStorage.getItem('ky_pet_mute'))).toBe('0')
  })

  test('主窗切换页面会把场景同步给桌宠窗口', async ({ page, context }) => {
    await login(page)
    await page.goto('/farm')
    await expect(page).toHaveURL(/\/farm/)

    const pet = await context.newPage()
    await pet.goto('/pet')
    await expect(pet.locator('.pet-bubble')).toContainText(/地块|农事/)

    await page.goto('/commander')
    await expect(page).toHaveURL(/\/commander$/)
    await expect(pet.locator('.pet-bubble')).toContainText(/计划卡|顺序|换人/)
    expect(await pet.evaluate(() => localStorage.getItem('ky_pet_scene'))).toBe('commander')
  })
})
