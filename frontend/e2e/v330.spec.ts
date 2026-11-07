import { expect, test } from '@playwright/test'
import { login } from './helpers'

test.describe('v3.30 语音转写（本机离线 / 在线通道）', () => {
  test('系统设置 → 语音转写：通道、本机模型与运行环境可见', async ({ page }) => {
    await login(page)
    await page.goto('/settings?tab=asr')

    await expect(page.getByText('语音转写通道')).toBeVisible()
    await expect(page.getByText('自动（本机优先，在线兜底）')).toBeVisible()
    await expect(page.getByRole('radio', { name: '仅本机（离线）' })).toBeVisible()
    await expect(page.getByRole('radio', { name: '仅在线' })).toBeVisible()
    await expect(page.getByText('本机模型', { exact: true })).toBeVisible()
    await expect(page.getByText('中文语音转写（轻量）')).toBeVisible()
    // 至少能看到「下载」或「删除」两个终态之一，且体积有展示
    await expect(page.locator('.model-row').first()).toContainText(/下载|删除/)
    await expect(page.locator('.model-row').first()).toContainText('MB')
    // 引擎状态与平台信息
    await expect(page.getByText('sherpa-onnx')).toBeVisible()
  })

  test('下载进度与校验状态按轮询渲染', async ({ page }) => {
    await login(page)
    await page.route('**/api/v1/asr/status', async route => {
      const resp = await route.fetch()
      const body = await resp.json()
      const model = body.data.models[0]
      body.data.mode = 'auto'
      body.data.effective = 'none'
      body.data.local.usable = false
      body.data.models[0] = {
        ...model,
        state: {
          status: 'downloading',
          downloaded: 20_971_520,
          total: 81_828_675,
          progress: 25,
          message: '下载中',
          installed_bytes: 20_971_520,
        },
      }
      await route.fulfill({ response: resp, json: body })
    })
    await page.goto('/settings?tab=asr')

    const row = page.locator('.model-row').first()
    await expect(row.locator('.el-progress')).toBeVisible()
    await expect(row.getByRole('button', { name: '取消' })).toBeVisible()
    await expect(row).toContainText('20MB')
  })

  test('对话页语音按钮存在且可访问', async ({ page }) => {
    await login(page)
    await page.goto('/chat')
    const mic = page.getByRole('button', { name: '语音输入' })
    await expect(mic).toBeVisible()
    await expect(mic).toBeEnabled()
  })
})
