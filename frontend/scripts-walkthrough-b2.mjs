/**
 * 拟人走查 Phase B2：用规范触发词重试对话内出图/出片（v3.73 媒体链路端到端）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[B2] ${s}: ${d}`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
const errors = []
page.on('pageerror', e => errors.push(String(e).slice(0, 120)))

await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 30000 })
await page.fill('input[placeholder="请输入用户名"]', 'admin')
await page.fill('input[type="password"]', 'admin123')
await page.keyboard.press('Enter')
await page.waitForTimeout(3500)

await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.click('button:has-text("新对话")').catch(() => {})
await page.waitForTimeout(1200)
const composer = page.locator('textarea').last()

// 出图（规范触发词）
await composer.fill('/图片 一张科技小院丰收季宣传海报：金色稻田、无人机飞过、阳光')
await page.keyboard.press('Enter')
log('出图', '已发送（/图片 触发词）')
await page.waitForTimeout(60000)
await page.screenshot({ path: `${OUT}/b07-image-retry-60s.png` })
await page.waitForTimeout(90000)
await page.screenshot({ path: `${OUT}/b08-image-retry-settled.png` })
log('出图', '150s 后截图')

// 出片（新对话，避免串扰）
await page.click('button:has-text("新对话")').catch(() => {})
await page.waitForTimeout(1200)
const composer2 = page.locator('textarea').last()
await composer2.fill('/视频 一段5秒的稻田航拍丰收画面')
await page.keyboard.press('Enter')
log('出片', '已发送（/视频 触发词）')
await page.waitForTimeout(150000)
await page.screenshot({ path: `${OUT}/b09-video-retry-150s.png` })
await page.waitForTimeout(120000)
await page.screenshot({ path: `${OUT}/b10-video-retry-settled.png` })
log('出片', '270s 后截图')

console.log('pageerrors:', errors.length ? errors.slice(0, 6) : 'none')
await browser.close()
