/**
 * 拟人化全页面遍历测试（交付前验收）：逐账号登录 → 遍历全部菜单路由 →
 * 收集每页的 console error / 失败请求 / 空白页，输出逐账号报告。
 *
 * 运行：cd frontend && node ../scripts-persona-walk.mjs（需 5173/8000 在跑）
 */
import { chromium } from '@playwright/test'

const BASE = 'http://localhost:5173'
const ACCOUNTS = [
  { user: 'admin', pass: 'admin123', label: '平台管理员+负责人' },
  { user: 'yard_admin', pass: 'yard123', label: '小院管理员' },
  { user: 'expert', pass: 'expert123', label: '专家' },
  { user: 'operator', pass: 'operator123', label: '研究生/技术员' },
]

/** 全部已注册路由（与 router/index.ts 对齐） */
const ROUTES_ALL = [
  '/', '/chat', '/commander', '/agents', '/agents/custom', '/agents/factory',
  '/teams', '/hubs', '/journal', '/knowledge', '/library', '/market',
  '/evolution', '/review', '/devices', '/farm', '/diagnosis', '/tasks',
  '/work-orders', '/org', '/members', '/roles', '/owner', '/quota',
  '/settings', '/model-center', '/bots', '/profile', '/workspace',
]

const ROLE_ROUTES = {
  admin: ROUTES_ALL,
  yard_admin: ROUTES_ALL,
  expert: ROUTES_ALL.filter(
    r => !['/members', '/roles', '/owner', '/quota', '/settings', '/bots', '/model-center', '/org'].includes(r),
  ),
  operator: ROUTES_ALL.filter(
    r => !['/agents/factory', '/teams', '/hubs', '/knowledge', '/library', '/market', '/evolution',
      '/review', '/devices', '/farm', '/diagnosis', '/tasks', '/work-orders', '/org', '/members',
      '/roles', '/owner', '/quota', '/settings', '/model-center', '/bots', '/workspace'].includes(r),
  ),
}

const results = []
const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } })
const page = await ctx.newPage()

let consoleErrors = []
let failedRequests = []
page.on('console', msg => {
  if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 160))
})
page.on('response', res => {
  if (res.status() >= 500) failedRequests.push(`${res.status()} ${res.url().slice(0, 120)}`)
})
page.on('pageerror', err => consoleErrors.push(`pageerror: ${String(err).slice(0, 160)}`))

for (const acc of ACCOUNTS) {
  // 登录（先清上一账号凭据，避免登录页被守卫重定向）
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 30000 })
  } catch {
    /* 守卫重定向导致的 ERR_ABORTED 不致命 */
  }
  await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 45000 })
  await page.fill('input[placeholder="请输入用户名"]', acc.user)
  await page.fill('input[type="password"]', acc.pass)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2500)
  const afterLogin = page.url()

  for (const route of ROLE_ROUTES[acc.user] ?? ROUTES_ALL) {
    consoleErrors = []
    failedRequests = []
    try {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 15000 })
      await page.waitForTimeout(2200)
      const body = await page.textContent('body')
      const blank = body.trim().length < 40
      const fatal = consoleErrors.filter(
        e => !e.includes('favicon') && !e.includes('ERR_ABORTED') && !e.includes('404'),
      )
      const errs = failedRequests.filter(u => !u.includes('404'))
      results.push({
        account: acc.user,
        route,
        ok: !blank && fatal.length === 0 && errs.length === 0,
        blank,
        console: fatal.slice(0, 2),
        http: errs.slice(0, 2),
      })
    } catch (e) {
      results.push({ account: acc.user, route, ok: false, blank: false,
        console: [`goto: ${String(e).slice(0, 120)}`], http: [] })
    }
  }
  // 登出
  await page.goto(`${BASE}/login`)
  await page.waitForTimeout(600)
}

await browser.close()

// 汇总
const bad = results.filter(r => !r.ok)
console.log(`\n===== 拟人化遍历报告：${results.length} 页次，${bad.length} 异常 =====`)
for (const r of bad) {
  console.log(`[FAIL] ${r.account} ${r.route}`)
  if (r.blank) console.log('   空白页')
  for (const c of r.console) console.log(`   console: ${c}`)
  for (const h of r.http) console.log(`   http: ${h}`)
}
console.log('\n各账号首登落地：', results.filter(r => r.route === '/').map(r => `${r.account}:${r.ok ? 'OK' : 'FAIL'}`).join(' '))
process.exit(bad.length ? 1 : 0)
