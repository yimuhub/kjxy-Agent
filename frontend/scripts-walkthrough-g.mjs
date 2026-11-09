/**
 * 拟人走查 Phase G：小白视角（operator 低权限账号）——首访引导、菜单密度、对话易用性。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[G] ${s}: ${d}`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })

// 小白：清空全部 localStorage（模拟全新用户），登录 operator
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 30000 })
await page.screenshot({ path: `${OUT}/g01-login-novice.png` })
await page.fill('input[placeholder="请输入用户名"]', 'operator')
await page.fill('input[type="password"]', 'operator123')
await page.keyboard.press('Enter')
await page.waitForTimeout(4000)
await page.screenshot({ path: `${OUT}/g02-novice-home.png` })
log('小白首页', page.url())

// 桌宠/伙伴在小白账号下是否可见
// 小白直接进对话，测「不填任何设置就能用」
await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
const composer = page.locator('textarea').last()
await composer.fill('土壤水分下降是什么原因？')
await page.keyboard.press('Enter')
log('小白提问', '已发送（普通模式）')
await page.waitForTimeout(60000)
await page.screenshot({ path: `${OUT}/g03-novice-chat-reply.png` })
log('小白对话', '60s 后截图')

// 退出登录（点右上角用户菜单）
await page.locator('.user-chip, [class*="user"]').last().click().catch(() => log('登出', '菜单定位失败'))
await page.waitForTimeout(800)
await page.screenshot({ path: `${OUT}/g04-user-menu.png` })

await browser.close()
