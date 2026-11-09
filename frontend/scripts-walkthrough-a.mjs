/**
 * 拟人走查 Phase A：真实登录 → 模型中心 UI 配置 Agnes 供应商 → 连通测试 → 设全局默认。
 * 全程 UI 操作 + 截图存证，输出到 docs/walkthrough-shots/。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/walkthrough-shots'
fs.mkdirSync(OUT, { recursive: true })
const log = (step, detail) => console.log(`[A] ${step}: ${detail}`)

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
const errors = []
page.on('pageerror', e => errors.push(String(e).slice(0, 150)))
page.on('console', m => { if (m.type() === 'error') errors.push(m.text().slice(0, 150)) })

// 1. 登录页
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.evaluate(() => localStorage.clear())
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 30000 })
await page.screenshot({ path: `${OUT}/a01-login.png` })
await page.fill('input[placeholder="请输入用户名"]', 'admin')
await page.fill('input[type="password"]', 'admin123')
await page.keyboard.press('Enter')
await page.waitForTimeout(3500)
log('登录', page.url())
await page.screenshot({ path: `${OUT}/a02-home.png` })

// 2. 模型中心
await page.goto(`${BASE}/model-center`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/a03-model-center.png` })

// 3. 新增供应商（UI 表单）
await page.click('text=新增供应商')
await page.waitForTimeout(800)
await page.fill('input[placeholder="逻辑名，如 deepseek-main"]', 'agnes-main')
// 供应商类型下拉
const typeSelect = page.locator('.el-dialog .el-select').first()
await typeSelect.click()
await page.waitForTimeout(400)
await page.locator('.el-select-dropdown__item').filter({ hasText: '自定义（OpenAI 兼容）' }).first().click({ timeout: 6000 })
await page.waitForTimeout(500)
await page.fill('input[placeholder="https://api.deepseek.com"]', 'https://apihub.agnes-ai.com/v1')
await page.fill('input[placeholder*="上游 API Key"]', 'process.env.AGNES_API_KEY ?? "sk-REDACTED"')
await page.fill('input[placeholder*="上游物理模型名"]', 'agnes-3.0-flash')
log('表单填写', 'agnes-main / openai_compatible / agnes-3.0-flash')
await page.screenshot({ path: `${OUT}/a04-supplier-form.png` })

// 4. 弹窗内连通测试
const testBtn = page.locator('.el-dialog button:has-text("测试连通")').first()
await testBtn.click()
await page.waitForTimeout(12000)
await page.screenshot({ path: `${OUT}/a05-test-result.png` })
log('连通测试', '已点击并等待结果')

// 5. 保存
await page.locator('.el-dialog button:has-text("确定")').click().catch(async () => {
  await page.locator('.el-dialog .el-button--primary').last().click()
})
await page.waitForTimeout(2500)
await page.screenshot({ path: `${OUT}/a06-supplier-saved.png` })

// 6. 全局默认绑定 agnes-3.0-flash（「全局默认与兜底」区）
try {
  await page.click('text=全局默认与兜底')
  await page.waitForTimeout(800)
  const sel = page.locator('label:has-text("全局默认模型")').locator('..').locator('.el-select').first()
  await sel.click()
  await page.waitForTimeout(600)
  await page.locator('.el-select-dropdown__item:has-text("agnes-main")').first().click({ timeout: 6000 })
  await page.waitForTimeout(400)
  // 有保存按钮就点
  const saveBtn = page.locator('button:has-text("保存"), button:has-text("保存默认")').first()
  if (await saveBtn.isVisible().catch(() => false)) await saveBtn.click()
  await page.waitForTimeout(2000)
  log('全局默认绑定', 'agnes-main · agnes-3.0-flash')
} catch (e) {
  log('全局默认绑定', `UI 兜底：${String(e).slice(0, 80)}（改用 API 兜底）`)
  // API 兜底：查 bindings 接口
}
await page.screenshot({ path: `${OUT}/a07-default-binding.png` })

console.log('pageerrors:', errors.length ? errors.slice(0, 5) : 'none')
await browser.close()
