/**
 * 拟人走查 Phase C：指挥官端到端——一句话目标 → 计划卡 → 开始执行 → 子任务 → 产物（真实 LLM）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[C] ${s}: ${d}`)

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

// 指挥官工作台页
await page.goto(`${BASE}/commander`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/c01-commander-home.png` })
log('指挥官页', '已打开')

// 对话页切指挥官模式发目标（主页输入区有 普通/指挥官 切换）
await page.goto(`${BASE}/chat`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2000)
await page.click('button:has-text("新对话")').catch(() => {})
await page.waitForTimeout(1000)
// 切指挥官：底部 composer 区域内的 pill 按钮（排除侧边栏菜单）
// el-segmented 分段控件：点选项里的「指挥官」label
const seg = page.locator('.el-segmented').last()
await seg.locator('.el-segmented__item, label, div').filter({ hasText: /^指挥官$/ }).first().click({ timeout: 8000 }).catch(async () => {
  await seg.click({ position: { x: 60, y: 10 } })
})
await page.waitForTimeout(900)
log('切换指挥官', '已点击分段选项')
const composer = page.locator('textarea').last()
await composer.fill('帮我写一段 80 字左右的本周工作小结，主题是设备巡检与工单处理，直接给结论。')
await page.keyboard.press('Enter')
log('目标已发', '等待指挥官计划卡生成…')
await page.waitForTimeout(50000)
await page.screenshot({ path: `${OUT}/c02-plan-card.png` })
log('计划卡', '50s 后截图')

// 查找并点击「开始执行」（计划卡上）
const execBtn = page.locator('button:has-text("开始执行"), button:has-text("执行")').last()
if (await execBtn.isVisible().catch(() => false)) {
  await execBtn.click()
  log('执行', '已点击开始执行')
} else {
  log('执行', '未找到执行按钮（可能自动执行或需在计划卡内找）')
}
await page.waitForTimeout(90000)
await page.screenshot({ path: `${OUT}/c03-executing.png` })
log('执行中', '90s 后截图')
await page.waitForTimeout(120000)
await page.screenshot({ path: `${OUT}/c04-exec-result.png` })
log('执行完成检查', '再等 120s 后截图')

// 指挥官工作台看运行记录与产物
await page.goto(`${BASE}/commander`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/c05-commander-runs.png` })

console.log('pageerrors:', errors.length ? errors.slice(0, 6) : 'none')
await browser.close()
