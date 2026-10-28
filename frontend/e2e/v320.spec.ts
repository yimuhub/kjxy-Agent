import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** v3.20 A2A 协作：设置页入口、入站令牌生成、协作方登记、入站 JSON-RPC 全链路。 */

const PEER_NAME = 'E2E 邻村'

test.describe('协作方（v3.20）', () => {
  test('管理员可查看协作页签，但不能修改', async ({ page }) => {
    await login(page)
    await page.goto('/settings?tab=a2a')
    await expect(page.getByRole('tab', { name: '协作方' })).toBeVisible()
    await expect(page.getByText('跨小院协作（A2A）')).toBeVisible()
    await expect(page.getByText('仅负责人或获得敏感授权的管理员可修改协作配置。')).toBeVisible()
    await expect(page.getByText('Agent Card')).toBeVisible()
    await expect(page.getByText('协作方（0）')).toBeVisible()
    // 管理员没有保护型权限：生成/轮换令牌按钮不出现
    await expect(page.getByRole('button', { name: /^(生成|轮换)令牌$/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '新增协作方' })).toHaveCount(0)
  })

  test('负责人生成入站令牌并可登记、删除协作方', async ({ page }) => {
    await login(page, 'admin', 'admin123')
    await page.goto('/settings?tab=a2a')

    await page.getByRole('button', { name: /^(生成|轮换)令牌$/ }).click()
    await page.getByRole('button', { name: /^(生成|轮换)$/ }).click()
    const tokenDialog = page.getByRole('dialog', { name: '入站令牌（只显示一次）' })
    await expect(tokenDialog).toBeVisible()
    await expect(tokenDialog.locator('.token-box')).toContainText('a2a_')
    await tokenDialog.getByRole('button', { name: '我已保存' }).click()
    await expect(page.locator('.settings-a2a')).toContainText('a2a_')

    // 清掉上次运行可能残留的同名协作方
    const existing = page.getByRole('row', { name: new RegExp(PEER_NAME) })
    if (await existing.count()) {
      await existing.first().getByRole('button', { name: '删除' }).click()
      await page.getByRole('button', { name: '确定' }).click()
      await expect(page.getByRole('row', { name: new RegExp(PEER_NAME) })).toHaveCount(0)
    }

    await page.getByRole('button', { name: '新增协作方' }).click()
    const dialog = page.getByRole('dialog', { name: '新增协作方' })
    await dialog.getByPlaceholder('如：邻村科技小院').fill(PEER_NAME)
    await dialog.getByPlaceholder('如：https://yard.example.com').fill('https://peer.e2e.invalid')
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.getByRole('row', { name: new RegExp(PEER_NAME) })).toBeVisible()
    await expect(page.locator('.settings-a2a')).toContainText('尚未探测')

    await page.getByRole('row', { name: new RegExp(PEER_NAME) }).getByRole('button', { name: '删除' }).click()
    await page.getByRole('button', { name: '确定' }).click()
    await expect(page.getByRole('row', { name: new RegExp(PEER_NAME) })).toHaveCount(0)
  })

  test('入站 JSON-RPC 全链路（需 A2A_ENABLED=true）', async ({ request }) => {
    const loginResp = await request.post('/api/v1/auth/login', {
      data: { username: 'admin', password: 'admin123' },
    })
    expect(loginResp.ok()).toBeTruthy()
    const access = (await loginResp.json()).data.access_token as string
    const headers = { Authorization: `Bearer ${access}` }

    const statusResp = await request.get('/api/v1/settings/a2a', { headers })
    expect(statusResp.ok()).toBeTruthy()
    test.skip(!(await statusResp.json()).data.enabled, '平台未开启 A2A')

    const tokenResp = await request.post('/api/v1/settings/a2a/token', { headers })
    expect(tokenResp.ok()).toBeTruthy()
    const raw = (await tokenResp.json()).data.token as string

    const rpc = await request.post('/api/v1/a2a/rpc', {
      headers: { Authorization: `Bearer ${raw}` },
      data: {
        jsonrpc: '2.0',
        id: 'e2e-1',
        method: 'message/send',
        params: {
          message: {
            messageId: `e2e-${Date.now()}`,
            role: 'user',
            parts: [{ kind: 'text', text: '概览' }],
            metadata: { skill: 'yard.summary' },
          },
        },
      },
    })
    expect(rpc.ok()).toBeTruthy()
    const body = await rpc.json()
    expect(body.result.status.state).toBe('completed')
    expect(body.result.artifacts[0].parts[0].text).toContain('地块')
  })
})
