import { expect, test } from '@playwright/test'
import { login } from './helpers'

/**
 * v3.48 多集群节点（平台管理员）：
 * a. 平台管理员在系统设置可见「集群节点」，可登记、探测、批量探测与删除；
 * b. 探测结果展示本机版本与迁移 head（在线）；
 * c. 小院管理员看不到该面板，直链也不能落到集群标签。
 */

const NODE_URL = process.env.E2E_CLUSTER_NODE_URL ?? 'http://127.0.0.1:8001'
const NODE_NAME = 'E2E 本机节点'

async function authHeaders(page: import('@playwright/test').Page) {
  const token = await page.evaluate(() => localStorage.getItem('ky_token'))
  return { Authorization: `Bearer ${token ?? ''}` }
}

test.describe('多集群节点（v3.48）', () => {
  test('平台管理员可登记、探测与删除节点', async ({ page }) => {
    await login(page, 'admin', 'admin123')

    // 清掉上次失败可能残留的同一地址节点，保证唯一约束不冲突
    const headers = await authHeaders(page)
    const existing = await page.request.get('/api/v1/cluster/nodes', { headers })
    if (existing.ok()) {
      const body = (await existing.json()) as {
        data: { items: { id: string; base_url: string }[] }
      }
      for (const item of body.data.items) {
        if (item.base_url === NODE_URL) {
          await page.request.delete(`/api/v1/cluster/nodes/${item.id}`, { headers })
        }
      }
    }

    const metaResp = await page.request.get('/api/v1/meta')
    expect(metaResp.ok()).toBeTruthy()
    const meta = (await metaResp.json()) as {
      data: { version: string; migration_head: string }
    }

    await page.goto('/settings?tab=cluster')
    const panel = page.locator('.cluster-panel')
    await expect(panel).toBeVisible()
    await expect(panel.locator('.panel-head h2')).toHaveText('集群节点')
    await expect(panel.locator('.summary-chips')).toContainText('共 0 个')

    await panel.getByRole('button', { name: '新增节点' }).click()
    const dialog = page.locator('.el-dialog', { hasText: '新增集群节点' })
    await expect(dialog).toBeVisible()
    await dialog.getByPlaceholder('例如：华东主节点').fill(NODE_NAME)
    await dialog.getByPlaceholder(/不要带 \/api 路径/).fill(NODE_URL)
    await dialog.getByRole('button', { name: '保存' }).click()
    await expect(page.locator('.el-message', { hasText: '节点已新增' })).toBeVisible()

    const row = panel.locator('.node-table .el-table__row', { hasText: NODE_NAME })
    await expect(row).toBeVisible()
    await expect(row).toContainText(NODE_URL)
    await expect(row.locator('.el-tag')).toContainText('未探测')

    await row.getByRole('button', { name: '探测' }).click()
    await expect(page.locator('.el-message', { hasText: '节点在线' })).toBeVisible()
    await expect(row.locator('.el-tag')).toContainText('在线')
    await expect(row).toContainText(meta.data.version)
    await expect(row).toContainText(meta.data.migration_head)
    await expect(row).not.toContainText('已过期')

    await panel.getByRole('button', { name: '全部探测' }).click()
    await expect(
      page.locator('.el-message', { hasText: /已探测 1 个节点：在线 1/ }),
    ).toBeVisible()
    await expect(panel.locator('.chip.online')).toContainText('在线 1')

    await row.getByRole('button', { name: '删除' }).click()
    const confirm = page.locator('.el-message-box', { hasText: '删除节点' })
    await expect(confirm).toBeVisible()
    await expect(confirm).toContainText('只移除登记，不影响远端实例')
    await confirm.getByRole('button', { name: '删除' }).click()
    await expect(page.locator('.el-message', { hasText: '节点已删除' })).toBeVisible()
    await expect(panel.locator('.el-empty')).toContainText('还没有登记节点')
    await expect(panel.locator('.chip.online')).toContainText('在线 0')
  })

  test('小院管理员不可见集群节点标签', async ({ page }) => {
    await login(page, 'yard_admin', 'yard123')
    await page.goto('/settings?tab=cluster')
    await expect(page.locator('.settings-tabs')).toBeVisible()
    await expect(page.locator('.el-tabs__item', { hasText: '集群节点' })).toHaveCount(0)
    await expect(page.locator('.cluster-panel')).toHaveCount(0)
    await expect(page).toHaveURL(/tab=cluster/)
    // 非法/无权限标签回落到第一个可用面板
    await expect(page.locator('.el-tabs__item.is-active')).toContainText('角色与权限')
  })
})
