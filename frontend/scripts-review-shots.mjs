/**
 * 五维度审查报告专用截图（v3.70）：
 * 定向抓取关键页面（含亮色模式与桌宠页），输出到 docs/review-shots/。
 * 用法：node scripts-review-shots.mjs（在 frontend/ 目录下执行）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const OUT = '../docs/review-shots'
fs.mkdirSync(OUT, { recursive: true })

const SHOTS = [
  // [文件名, 账号, 路由, 等待ms, 备注]
  ['01-login', null, '/login', 2500, '小白第一眼：登录页'],
  ['02-setup-check', null, '/', 2500, '未登录访问根路径的分流'],
  ['03-home-dark', 'admin', '/', 3200, '概览（暗色）'],
  ['04-home-light', 'admin', '/', 3200, '概览（亮色）'],
  ['05-chat', 'admin', '/chat', 3500, '智能对话 + 悬浮伙伴'],
  ['06-commander', 'admin', '/commander', 3500, '指挥官工作台'],
  ['07-agents-factory', 'admin', '/agents/factory', 3200, '智能体工厂'],
  ['08-tasks', 'admin', '/tasks', 3000, '任务中心'],
  ['09-work-orders', 'admin', '/work-orders', 3000, '工单'],
  ['10-farm', 'admin', '/farm', 3200, '农业台账'],
  ['11-diagnosis', 'admin', '/diagnosis', 3000, '病虫害诊断'],
  ['12-knowledge', 'admin', '/knowledge', 3000, '知识库'],
  ['13-market', 'admin', '/market', 3000, '技能市场'],
  ['14-quota', 'admin', '/quota', 3500, '配额看板'],
  ['15-workspace', 'admin', '/workspace', 3200, '角色工作台'],
  ['16-journal', 'admin', '/journal', 3000, '日志工作台'],
  ['17-settings', 'admin', '/settings', 3200, '系统设置'],
  ['18-pet', 'admin', '/pet', 2800, '桌宠页（浏览器预览）'],
  ['19-yard-admin-home', 'yard_admin', '/', 3200, '小院管理员视角'],
  ['20-operator-home', 'operator', '/', 3200, '研究生（低权限）视角'],
]

const CREDS = {
  admin: { user: 'admin', pass: 'admin123' },
  yard_admin: { user: 'yard_admin', pass: 'yard123' },
  operator: { user: 'operator', pass: 'operator123' },
}

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } })
const page = await ctx.newPage()

let current = null
async function loginAs(key) {
  if (current === key) return
  current = key
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 45000 })
  await page.fill('input[placeholder="请输入用户名"]', CREDS[key].user)
  await page.fill('input[type="password"]', CREDS[key].pass)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(3200)
}

for (const [name, acc, route, wait] of SHOTS) {
  try {
    if (acc) {
      await loginAs(acc)
    } else {
      current = null
      await page.goto(BASE, { waitUntil: 'domcontentloaded' })
      await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
    }
    if (name === '04-home-light') {
      await page.evaluate(() => localStorage.setItem('kjxy-theme-mode', 'light'))
    } else {
      // 其余全部固定暗色，避免上一张的亮色状态泄漏
      await page.evaluate(() => localStorage.setItem('kjxy-theme-mode', 'dark'))
    }
    await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await page.waitForTimeout(wait)
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.waitForTimeout(800)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.waitForTimeout(500)
    await page.screenshot({ path: `${OUT}/${name}.jpg`, type: 'jpeg', quality: 82 })
    console.log(`ok ${name}`)
  } catch (err) {
    console.log(`FAIL ${name}: ${String(err).slice(0, 120)}`)
  }
}

await browser.close()
console.log('done')
