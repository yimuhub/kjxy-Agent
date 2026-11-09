/**
 * 拟人走查 Phase B：智能对话真实 LLM 对话 + 对话内出图/出片（全 UI 操作）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[B] ${s}: ${d}`)

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
log('登录', page.url())

// 1. 对话页，真实 LLM 对话
await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
// 新对话
await page.click('button:has-text("新对话")').catch(() => log('新对话', '按钮未找到，沿用当前'))
await page.waitForTimeout(1200)
const composer = page.locator('textarea[placeholder*="说"], textarea[placeholder*="Enter"], .chat-composer textarea, textarea').last()
await composer.fill('用三句话向一位新来的驻点研究生介绍这个平台能帮他做什么。')
await page.screenshot({ path: `${OUT}/b01-chat-typed.png` })
await page.keyboard.press('Enter')
log('发送', '第一问已发送，等待 LLM 流式回复…')
// 等待回复出现（流式结束的判据：发送按钮恢复/等待稳定）
await page.waitForTimeout(60000)
const textLen1 = (await page.locator('.msg, [class*="message"]').count().catch(() => 0))
await page.screenshot({ path: `${OUT}/b02-chat-reply.png` })
log('第一轮回复', `消息节点 ${textLen1} 个，已截图（等 60s 后）`)
await page.waitForTimeout(45000)
await page.screenshot({ path: `${OUT}/b03-chat-reply-settled.png` })
log('回复稳定', '再等 45s 后截图')

// 2. 对话内出图（v3.73）
await composer.fill('/画 一张科技小院丰收季宣传海报：金色稻田、无人机飞过、阳光，下方留白写"科技小院"')
await page.keyboard.press('Enter')
log('出图', '海报请求已发送，等待媒体卡片…')
await page.waitForTimeout(90000)
await page.screenshot({ path: `${OUT}/b04-image-gen.png` })
log('出图', '90s 后截图（若生成慢记录中间态）')
await page.waitForTimeout(90000)
await page.screenshot({ path: `${OUT}/b05-image-gen-settled.png` })

// 3. 对话内出片（视频，长耗时，记录中间态）
await composer.fill('/视频 一段5秒的稻田航拍丰收画面')
await page.keyboard.press('Enter')
log('出片', '视频请求已发送')
await page.waitForTimeout(120000)
await page.screenshot({ path: `${OUT}/b06-video-gen.png` })

console.log('pageerrors:', errors.length ? errors.slice(0, 6) : 'none')
await browser.close()
