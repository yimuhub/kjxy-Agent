/**
 * 拟人走查 Phase F：套餐兑换全闭环（UI 发码 → 输码兑换 → 额度到账）+ 一句话生成智能体（Phase D 合并）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (s, d) => console.log(`[F] ${s}: ${d}`)

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

// 1. 兑换码生成（UI）→ 拦截剪贴板拿码 → 输码兑换
await page.goto(`${BASE}/quota`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.click('text=套餐与兑换')
await page.waitForTimeout(1400)
// 拦截生成码请求拿码值（比读剪贴板稳）
let codeValue = ''
page.on('response', async res => {
  if (res.url().includes('/billing/codes') && res.request().method() === 'POST') {
    try {
      const j = await res.json()
      codeValue = j?.data?.[0]?.code ?? ''
    } catch { /* ignore */ }
  }
})
await page.locator('button:has-text("生成兑换码")').click()
await page.waitForTimeout(2500)
log('发码', codeValue ? `已生成 ${codeValue.slice(0, 8)}…` : '未捕获到码（检查响应）')
await page.screenshot({ path: `${OUT}/f01-codes-generated.png` })

if (codeValue) {
  // 点基础版「输码兑换」→ 弹窗输入码 → 兑换到账
  await page.locator('.plan-card:has-text("基础版") button:has-text("输码兑换")').click()
  await page.waitForTimeout(900)
  await page.fill('.el-dialog input[placeholder*="兑换码"]', codeValue)
  await page.screenshot({ path: `${OUT}/f02-redeem-dialog.png` })
  await page.locator('.el-dialog button:has-text("兑换到账")').click()
  await page.waitForTimeout(3000)
  await page.screenshot({ path: `${OUT}/f03-redeem-result.png` })
  log('兑换', '已提交，截图确认到账提示')
}

// 2. 概览核对额度卡片变化
await page.goto(`${BASE}/quota`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.click('text=概览').catch(() => {})
await page.waitForTimeout(1500)
await page.screenshot({ path: `${OUT}/f04-quota-overview-after.png` })

// 3. 一句话生成智能体（智能工厂）
await page.goto(`${BASE}/agents/factory`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/f05-factory.png` })
const genInput = page.locator('textarea[placeholder*="一句话"], textarea[placeholder*="描述"], textarea').last()
await genInput.fill('帮我创建一个「稻田水分周报助手」：每周汇总土壤水分告警，输出一段 100 字周报摘要。')
await page.keyboard.press('Enter').catch(() => {})
const genBtn = page.locator('button:has-text("生成")').last()
if (await genBtn.isVisible().catch(() => false)) await genBtn.click()
log('生成智能体', '一句话已提交，等待任务完成…')
await page.waitForTimeout(60000)
await page.screenshot({ path: `${OUT}/f06-agent-gen-60s.png` })
await page.waitForTimeout(60000)
await page.screenshot({ path: `${OUT}/f07-agent-gen-120s.png` })

// 任务中心看生成任务终态
await page.goto(`${BASE}/tasks`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2200)
await page.screenshot({ path: `${OUT}/f08-tasks-terminal.png` })

console.log('pageerrors:', errors.length ? errors.slice(0, 6) : 'none')
await browser.close()
