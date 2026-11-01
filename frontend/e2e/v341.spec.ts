import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.41 系统日志：
 * a. 负责人能在设置页直接看到脱敏运行日志与文件元信息；
 * b. 「只看错误」→ level=ERROR，「桌面引擎日志」→ source=engine。
 *
 * 不依赖真实模型；日志文件由后端启动时创建。
 */

test.describe('系统日志（v3.41）', () => {
  test('负责人查看日志并按级别/来源筛选', async ({ page }) => {
    const [initial] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'GET' &&
          /\/api\/v1\/system\/logs(\?|$)/.test(resp.url()) &&
          resp.url().includes('source=app'),
        { timeout: 30_000 },
      ),
      (async () => {
        await login(page, 'admin', 'admin123')
        await page.goto('/settings?tab=system_logs')
      })(),
    ])
    expect(initial.ok()).toBeTruthy()
    const body = (await initial.json()) as {
      data: { items: unknown[]; meta: { source: string; path: string } }
    }
    expect(body.data.meta.source).toBe('app')
    expect(body.data.meta.path).toContain('app.log')
    expect(Array.isArray(body.data.items)).toBe(true)

    const section = page.locator('.settings-logs')
    await expect(section).toBeVisible()
    await expect(section.getByText('系统日志', { exact: true }).first()).toBeVisible()
    await expect(section.locator('.meta')).toContainText('app.log')

    // 只看错误 → 请求带 level=ERROR
    const [filtered] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'GET' &&
          resp.url().includes('/api/v1/system/logs?') &&
          resp.url().includes('level=ERROR'),
        { timeout: 30_000 },
      ),
      section.getByRole('button', { name: '只看错误' }).click(),
    ])
    expect(filtered.ok()).toBeTruthy()

    // 切到桌面引擎日志 → source=engine（文件不存在也返回 200 + exists=false）
    const [engine] = await Promise.all([
      page.waitForResponse(
        resp =>
          resp.request().method() === 'GET' &&
          resp.url().includes('/api/v1/system/logs?') &&
          resp.url().includes('source=engine'),
        { timeout: 30_000 },
      ),
      (async () => {
        await section.locator('.w-source .el-select__wrapper').click()
        await page
          .locator('.el-select-dropdown__item', { hasText: '桌面引擎日志' })
          .last()
          .click()
      })(),
    ])
    expect(engine.ok()).toBeTruthy()
    expect(((await engine.json()) as { data: { meta: { source: string } } }).data.meta.source).toBe(
      'engine',
    )
  })
})
