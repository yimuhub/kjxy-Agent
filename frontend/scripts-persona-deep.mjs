/**
 * 交付前深度拟人化扫描 v2（v3.65.1）：
 * 逐账号登录 → 逐可见页面（滚动触发懒加载）→ 截图入库 + 深度检测
 * （console error / 5xx / 非 401-403 的 4xx / 空白主区 / 空态白屏）。
 * 同时产出每账号首屏截图，供用户说明书使用。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const BASE = 'http://localhost:5173'
const SHOT_DIR = '../docs/manual/screenshots'
fs.mkdirSync(SHOT_DIR, { recursive: true })

const ACCOUNTS = [
  { user: 'admin', pass: 'admin123', label: 'platform-admin' },
  { user: 'yard_admin', pass: 'yard123', label: 'yard-admin' },
  { user: 'expert', pass: 'expert123', label: 'expert' },
  { user: 'operator', pass: 'operator123', label: 'operator' },
]

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
const ctx = await browser.newContext({ viewport: { width: 1600, height: 950 } })
const page = await ctx.newPage()

let consoleErrors = []
let badRequests = []
page.on('console', msg => {
  if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 200))
})
page.on('response', res => {
  const u = res.url()
  if (!u.includes('/api/')) return
  if (res.status() >= 500) badRequests.push(`5xx ${res.status()} ${u.replace(BASE + '/api/v1', '')}`)
  // 4xx 审查：401/403/404 属常见拒绝；409/422 等出现在纯浏览页面即可疑
  if (res.status() === 409 || res.status() === 422 || res.status() === 402) {
    badRequests.push(`4xx ${res.status()} ${u.replace(BASE + '/api/v1', '')}`)
  }
})
page.on('pageerror', err => consoleErrors.push(`pageerror: ${String(err).slice(0, 200)}`))

for (const acc of ACCOUNTS) {
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded', timeout: 30000 })
  } catch { /* 守卫重定向容忍 */ }
  await page.waitForSelector('input[placeholder="请输入用户名"]', { timeout: 45000 })
  await page.fill('input[placeholder="请输入用户名"]', acc.user)
  await page.fill('input[type="password"]', acc.pass)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(3200)

  // 首屏截图（说明书用）
  await page.screenshot({ path: `${SHOT_DIR}/${acc.label}-home.png`, fullPage: false })

  for (const route of ROLE_ROUTES[acc.user] ?? ROUTES_ALL) {
    consoleErrors = []
    badRequests = []
    const slug = route === '/' ? 'home' : route.replace(/\//g, '_').replace(/^_/, '')
    const shot = `${SHOT_DIR}/${acc.label}${slug === 'home' ? '-home' : slug}.png`
    try {
      try {
        await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
      } catch { /* 重定向容忍 */ }
      await page.waitForTimeout(2600)
      // 滚动触发懒加载
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
      await page.waitForTimeout(900)
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.waitForTimeout(400)

      const bodyLen = (await page.textContent('body')).trim().length
      // 主区域空白检测：排除登录重定向页
      const mainEmpty = bodyLen < 60 && !page.url().includes('/login')

      await page.screenshot({ path: shot, fullPage: false })

      const fatalConsole = consoleErrors.filter(
        e => !e.includes('favicon') && !e.includes('ERR_ABORTED') &&
             !e.includes('404') && !e.includes('Failed to load resource: the server responded with a status of 40'),
      )
      const fatalHttp = badRequests.filter(h => !h.includes('404'))
      const ok = !mainEmpty && fatalConsole.length === 0 && fatalHttp.length === 0
      results.push({ account: acc.user, route, ok, mainEmpty, shot,
        console: fatalConsole.slice(0, 2), http: fatalHttp.slice(0, 2) })
    } catch (e) {
      results.push({ account: acc.user, route, ok: false, mainEmpty: false, shot: '',
        console: [`goto: ${String(e).slice(0, 140)}`], http: [] })
    }
  }
  // 登出清凭据
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
}

await browser.close()
fs.writeFileSync(`${SHOT_DIR}/scan-results.json`, JSON.stringify(results, null, 2))

const bad = results.filter(r => !r.ok)
console.log(`\n===== 深度扫描：${results.length} 页次，异常 ${bad.length} =====`)
for (const r of bad) {
  console.log(`[FAIL] ${r.account} ${r.route}`)
  if (r.mainEmpty) console.log('   主区域空白')
  for (const c of r.console) console.log(`   console: ${c}`)
  for (const h of r.http) console.log(`   http: ${h}`)
}
console.log(`截图：${results.filter(r => r.shot).length} 张 → ${SHOT_DIR}`)
process.exit(bad.length ? 1 : 0)
