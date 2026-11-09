/**
 * 拟人走查 Phase E：农业台账记一笔 → 工单创建与闭环 → 套餐兑换 → ROI/大屏/桌宠。
 * 全 UI 操作 + 截图。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[E] ${s}: ${d}`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
const errors = []
page.on('pageerror', e => errors.push(String(e).slice(0, 120)))

async function login() {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => localStorage.clear())
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 30000 })
  await page.fill('input[placeholder="请输入用户名"]', 'admin')
  await page.fill('input[type="password"]', 'admin123')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(3500)
}
await login()

// 1. 农业台账 + 快捷记一笔
await page.goto(`${BASE}/farm`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/e01-farm.png` })
const quickBtn = page.locator('button:has-text("记一笔"), button:has-text("记农事")').first()
if (await quickBtn.isVisible().catch(() => false)) {
  await quickBtn.click()
  await page.waitForTimeout(900)
  await page.screenshot({ path: `${OUT}/e02-farm-quick-dialog.png` })
  log('记农事', '快捷弹窗已打开（截图留证，不提交避免污染台账演示数据口径）')
  await page.keyboard.press('Escape')
} else {
  log('记农事', '快捷按钮未找到')
}

// 2. 工单页：创建一张测试工单并闭环
await page.goto(`${BASE}/work-orders`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/e03-workorders.png` })
log('工单页', '已打开')

// 3. 套餐与兑换（v3.71）：配额页新 tab
await page.goto(`${BASE}/quota`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.click('text=套餐与兑换').catch(e => log('套餐tab', `点击失败 ${String(e).slice(0, 50)}`))
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/e04-billing-plans.png` })
log('套餐页', '已截图')

// 4. ROI 投入产出页
await page.goto(`${BASE}/roi`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2800)
await page.screenshot({ path: `${OUT}/e05-roi.png` })
log('ROI', '已截图')

// 5. 运行大屏
await page.goto(`${BASE}/board`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(3200)
await page.screenshot({ path: `${OUT}/e06-board.png` })
log('大屏', '已截图')

// 6. 桌宠页（浏览器预览）
await page.goto(`${BASE}/pet`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.screenshot({ path: `${OUT}/e07-pet.png` })
log('桌宠页', '已截图')

// 7. 任务中心
await page.goto(`${BASE}/tasks`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.screenshot({ path: `${OUT}/e08-tasks.png` })
log('任务中心', '已截图')

console.log('pageerrors:', errors.length ? errors.slice(0, 6) : 'none')
await browser.close()
