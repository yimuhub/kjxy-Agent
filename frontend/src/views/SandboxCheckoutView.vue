<template>
  <div class="sandbox-checkout-page">
    <div class="checkout-card">
      <div class="brand">
        <img
          v-if="branding.logoUrl"
          :src="branding.logoUrl"
          alt=""
          class="logo-img"
        >
        <h1 class="wordmark">
          沙箱收银台
        </h1>
        <p class="slogan">
          仅用于联调演示：点击确认后走真实回调端点完成到账闭环。
        </p>
      </div>

      <el-alert
        type="warning"
        :closable="false"
        class="sandbox-note"
      >
        沙箱渠道不产生任何真实扣款；生产部署已禁用该渠道。
      </el-alert>

      <div
        v-if="!orderId"
        class="state-block"
      >
        <el-result
          icon="warning"
          title="缺少订单号"
          sub-title="请从「套餐与兑换」页的支付对话框进入沙箱收银台。"
        >
          <template #extra>
            <el-button
              type="primary"
              @click="goBilling"
            >
              返回套餐页
            </el-button>
          </template>
        </el-result>
      </div>

      <template v-else>
        <div
          v-if="state === 'paying'"
          v-loading="true"
          class="state-block"
          element-loading-text="正在生成沙箱支付凭据…"
        />
        <el-result
          v-else-if="state === 'success'"
          icon="success"
          title="支付成功，额度已到账"
          :sub-title="`订单 ${orderId.slice(0, 8)} 已通过沙箱回调完成幂等结算。`"
        >
          <template #extra>
            <el-button
              type="primary"
              @click="goBilling"
            >
              查看额度与订单
            </el-button>
          </template>
        </el-result>
        <el-result
          v-else
          icon="error"
          title="沙箱支付未完成"
          :sub-title="message"
        >
          <template #extra>
            <el-button
              type="primary"
              :loading="retrying"
              @click="pay"
            >
              重试
            </el-button>
            <el-button @click="goBilling">
              返回套餐页
            </el-button>
          </template>
        </el-result>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
  /**
   * 沙箱收银台（v3.80 补口）：后端 SandboxProvider 生成的支付意图把
   * ``pay_url`` / ``qr_payload`` 指向本页（``/billing/sandbox/checkout?order=…``），
   * 此前前端没有这个路由——扫码/点击链接直接 404，沙箱链路只剩
   * 计费面板里「我已完成支付」按钮一条路。本页补齐设计意图：
   * 「沙箱收银台就是平台自己的一个回调入口」，进入后自动走
   * prepare（生成签名载荷）→ notify（真实公开回调端点）→ 幂等结算。
   */
  import { onMounted, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import { billingApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import { friendlyErrorMessage } from '@/utils/status'
  import { useBrandingStore } from '@/stores/branding'

  const route = useRoute()
  const router = useRouter()
  const branding = useBrandingStore()

  const orderId = ref('')
  const state = ref<'paying' | 'success' | 'error'>('paying')
  const message = ref('')
  const retrying = ref(false)

  function goBilling(): void {
    void router.replace({ path: '/quota', query: { tab: 'billing' } })
  }

  async function pay(): Promise<void> {
    retrying.value = true
    try {
      const { payload, notify_url } = await billingApi.sandboxPrepare(orderId.value)
      await billingApi.sandboxNotify(notify_url, payload)
      state.value = 'success'
    } catch (err: unknown) {
      const { code, message: errMessage } = extractErrorMessage(err)
      message.value = friendlyErrorMessage(code, errMessage)
      state.value = 'error'
    } finally {
      retrying.value = false
    }
  }

  onMounted(() => {
    orderId.value = typeof route.query.order === 'string' ? route.query.order : ''
    if (!orderId.value) {
      state.value = 'error'
      message.value = ''
      return
    }
    void pay()
  })
</script>

<style scoped>
  .sandbox-checkout-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 0;
    box-sizing: border-box;
  }
  .checkout-card {
    width: min(460px, 92vw);
    padding: 36px 32px 26px;
    background: var(--ds-bg-1);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card, 16px);
    box-shadow: var(--ds-shadow-card);
  }
  .brand {
    text-align: center;
    margin-bottom: 16px;
  }
  .logo-img {
    width: 44px;
    height: 44px;
    border-radius: 12px;
    margin-bottom: 10px;
  }
  .wordmark {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .slogan {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .sandbox-note {
    border-radius: 10px;
    margin-bottom: 14px;
  }
  .state-block {
    height: 120px;
  }
</style>
