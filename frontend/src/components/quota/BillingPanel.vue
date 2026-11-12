<template>
  <div
    class="billing"
    data-billing-panel
  >
    <el-alert
      type="info"
      :closable="false"
      class="billing-note"
    >
      下单后可直接在本页自助完成支付：扫码支付即时到账；对公转账提交回单后，由平台核对回单真伪，核对通过即自动写入额度。
    </el-alert>

    <!-- 当前用量与升级引导（v3.80 审查 D-19/D-20/D-21） -->
    <div
      v-if="usage"
      class="usage-box"
      data-billing-usage
    >
      <div class="usage-head">
        <strong>当前额度水位</strong>
        <span
          v-if="usage.needs_attention"
          class="usage-warn"
        >已用 {{ usage.max_usage_percent }}%，建议尽快升级</span>
        <span
          v-else
          class="muted"
        >已用 {{ usage.max_usage_percent }}%</span>
      </div>
      <ul class="usage-metrics">
        <li
          v-for="m in usage.metrics"
          :key="m.metric"
        >
          <span class="usage-label">{{ m.metric_label }}</span>
          <span class="usage-value">
            <template v-if="m.unlimited">
              不限量（已用 {{ formatAmount(m.used) }}）
            </template>
            <template v-else-if="!m.limit">
              已用 {{ formatAmount(m.used) }} {{ m.unit }} · 上限未设置
            </template>
            <template v-else>
              {{ formatAmount(m.used) }} / {{ formatAmount(m.limit) }} {{ m.unit }}
            </template>
          </span>
          <el-progress
            v-if="m.limit"
            :percentage="Math.min(100, m.usage_percent)"
            :status="m.usage_percent >= 90 ? 'exception' : m.usage_percent >= 70 ? 'warning' : 'success'"
            :stroke-width="8"
            :show-text="false"
          />
        </li>
      </ul>
      <p
        v-if="usage.anchor.based_on_calls > 0 && usage.anchor.calls_remaining > 0"
        class="usage-anchor"
      >
        按近 {{ usage.window_days }} 天平均每次 {{ formatAmount(usage.anchor.tokens_per_call) }} Token 估算，
        剩余额度约还能问 <strong>{{ usage.anchor.calls_remaining.toLocaleString() }}</strong> 次。
      </p>
      <div
        v-if="recommendation?.recommended_name"
        class="usage-recommend"
      >
        <el-tag
          type="warning"
          size="small"
          effect="light"
        >
          升级建议
        </el-tag>
        <span class="usage-recommend-text">{{ recommendation.reason }}</span>
        <el-button
          size="small"
          type="primary"
          link
          @click="jumpToPlan(recommendation.recommended_code)"
        >
          查看 {{ recommendation.recommended_name }}
        </el-button>
      </div>
    </div>

    <!-- 套餐目录 -->
    <div class="plan-grid">
      <div
        v-for="plan in plans"
        :key="plan.code"
        class="plan-card"
        :class="{ booster: plan.kind === 'booster' }"
        :data-plan="plan.code"
      >
        <div class="plan-head">
          <strong>{{ plan.name }}</strong>
          <span class="plan-price">¥{{ formatPrice(plan.price_yuan) }}</span>
        </div>
        <p class="plan-tagline">
          {{ plan.tagline }}
        </p>
        <!-- 额度明细：卖什么一目了然（审查 D-18/D-21） -->
        <ul
          v-if="(plan.entitlement?.length ?? 0) > 0"
          class="plan-quota"
        >
          <li
            v-for="(e, i) in plan.entitlement"
            :key="i"
          >
            <span class="plan-quota-label">{{ e.metric_label }}</span>
            <span class="plan-quota-value">{{ formatAmount(e.total) }} {{ e.unit }}</span>
            <span
              v-if="!plan.one_off && e.base > 0"
              class="plan-quota-base"
            >（含环境基础 {{ formatAmount(e.base) }}）</span>
          </li>
        </ul>
        <ul
          v-if="(plan.highlights?.length ?? 0) > 0"
          class="plan-items"
        >
          <li
            v-for="(h, i) in plan.highlights"
            :key="`h${i}`"
          >
            {{ h }}
          </li>
        </ul>
        <ul
          v-else-if="(plan.items?.length ?? 0) > 0"
          class="plan-items"
        >
          <li
            v-for="(item, i) in plan.items"
            :key="i"
          >
            {{ item.note }}
          </li>
        </ul>
        <div class="plan-actions">
          <el-button
            size="small"
            type="primary"
            :loading="buyingPlan === plan.code"
            :disabled="!canManage"
            @click="buyNow(plan)"
          >
            立即开通
          </el-button>
          <el-button
            size="small"
            :disabled="!canManage"
            @click="openRedeemWith(plan)"
          >
            输码兑换
          </el-button>
          <el-button
            size="small"
            :disabled="!canManage"
            @click="openOrderWith(plan)"
          >
            下单
          </el-button>
        </div>
      </div>
    </div>
    <p
      v-if="!canManage"
      class="muted"
    >
      兑换与下单需要小院管理员权限；当前账号只读。
    </p>

    <!-- 我的订单 -->
    <div class="orders">
      <h4>本小院订单</h4>
      <el-table
        v-col-resize
        :data="orders"
        size="small"
        empty-text="还没有订单"
      >
        <el-table-column
          prop="plan_code"
          label="套餐"
          width="140"
        >
          <template #default="{ row }">{{ planName(row.plan_code) }}</template>
        </el-table-column>
        <el-table-column
          prop="price_yuan"
          label="金额"
          width="110"
        >
          <template #default="{ row }">¥{{ formatPrice(row.price_yuan) }}</template>
        </el-table-column>
        <el-table-column
          prop="status"
          label="状态"
          width="110"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="statusType(row.status)"
            >{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="voucher_note"
          label="凭证说明"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column
          prop="created_at"
          label="时间"
          width="160"
        >
          <template #default="{ row }">{{ shortTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column
          label=""
          width="150"
        >
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'pending'"
              size="small"
              type="primary"
              link
              :loading="payingOrder === row.id"
              @click="openPay(row as BillingOrder)"
            >
              去支付
            </el-button>
            <el-button
              v-if="row.status === 'pending'"
              size="small"
              link
              @click="cancel(row.id)"
            >
              取消
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 平台管理员：发码 / 全部订单 -->
    <template v-if="isPlatformAdmin">
      <div class="admin-zone">
        <h4>平台管理员 · 兑换码</h4>
        <div class="admin-row">
          <el-select
            v-model="codePlan"
            size="small"
            style="width: 200px"
          >
            <el-option
              v-for="plan in plans"
              :key="plan.code"
              :label="plan.name"
              :value="plan.code"
            />
          </el-select>
          <el-input-number
            v-model="codeCount"
            size="small"
            :min="1"
            :max="200"
            style="width: 130px"
          />
          <el-button
            size="small"
            type="primary"
            @click="genCodes"
          >
            生成兑换码
          </el-button>
        </div>
        <el-table
          v-col-resize
          :data="codes"
          size="small"
          max-height="260"
          empty-text="还没有兑换码"
        >
          <el-table-column
            prop="code"
            label="码"
            min-width="180"
          />
          <el-table-column
            prop="plan_code"
            label="套餐"
            width="130"
          >
            <template #default="{ row }">{{ planName(row.plan_code) }}</template>
          </el-table-column>
          <el-table-column
            prop="status"
            label="状态"
            width="100"
          >
            <template #default="{ row }">{{ codeLabel(row.status) }}</template>
          </el-table-column>
          <el-table-column
            prop="redeemed_at"
            label="兑换时间"
            width="160"
          >
            <template #default="{ row }">{{ shortTime(row.redeemed_at) }}</template>
          </el-table-column>
          <el-table-column
            label=""
            width="80"
          >
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'unused'"
                size="small"
                link
                @click="disable(row.id)"
              >
                作废
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="admin-zone">
        <h4>平台管理员 · 待确认订单</h4>
        <el-table
          v-col-resize
          :data="allOrders"
          size="small"
          max-height="260"
          empty-text="没有订单"
        >
          <el-table-column
            prop="yard_id"
            label="小院"
            width="200"
            show-overflow-tooltip
          />
          <el-table-column
            prop="plan_code"
            label="套餐"
            width="130"
          >
            <template #default="{ row }">{{ planName(row.plan_code) }}</template>
          </el-table-column>
          <el-table-column
            prop="status"
            label="状态"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="statusType(row.status)"
              >{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="凭证 / 回单"
            min-width="200"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span>{{ row.voucher_note || '-' }}</span>
              <el-tag
                v-if="row.voucher_status"
                size="small"
                :type="row.voucher_status === 'manual' ? 'danger' : 'warning'"
                style="margin-left: 6px"
              >
                {{ row.voucher_status === 'manual' ? '金额不符' : '回单待核验' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label=""
            width="110"
          >
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'pending'"
                size="small"
                type="primary"
                link
                @click="confirm(row.id)"
              >
                确认到账
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <!-- 兑换 / 下单对话框 -->
    <el-dialog
      v-model="redeemOpen"
      append-to-body
      :title="`兑换 · ${activePlan?.name ?? ''}`"
      width="420px"
    >
      <el-input
        v-model="redeemCode"
        placeholder="输入兑换码，如 KY-1A2B-3C4D-5E6F"
        maxlength="24"
      />
      <template #footer>
        <el-button @click="redeemOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="busy"
          @click="doRedeem"
        >
          兑换到账
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="orderOpen"
      append-to-body
      :title="`下单 · ${activePlan?.name ?? ''}`"
      width="460px"
    >
      <p class="muted">
        应付金额：¥{{ activePlan ? formatPrice(activePlan.price_yuan) : '-' }}。
        下单后可在订单列表点「去支付」完成付款；加油包请附转账凭证说明。
      </p>
      <el-input
        v-model="orderNote"
        type="textarea"
        :rows="3"
        placeholder="转账凭证说明：回单号 / 付款方 / 日期"
        maxlength="500"
      />
      <template #footer>
        <el-button @click="orderOpen = false">取消</el-button>
        <el-button
          type="primary"
          :loading="busy"
          @click="doOrder"
        >
          提交订单
        </el-button>
      </template>
    </el-dialog>

    <!-- 支付对话框：扫码支付 / 对公转账回单 -->
    <el-dialog
      v-model="payOpen"
      append-to-body
      :title="`支付 · ${intent?.plan_name ?? ''}`"
      width="460px"
      destroy-on-close
    >
      <template v-if="intent">
        <div class="pay-summary">
          <span class="pay-amount">¥{{ formatPrice(intent.amount_yuan) }}</span>
          <span class="muted">订单号 {{ intent.order_code }}</span>
        </div>

        <template v-if="intent.mode === 'qr'">
          <div
            v-if="qrDataUrl"
            class="pay-qr"
          >
            <img
              :src="qrDataUrl"
              alt="支付二维码"
            >
          </div>
          <ol class="pay-steps">
            <li
              v-for="(line, i) in intent.instructions"
              :key="i"
            >
              {{ line }}
            </li>
          </ol>
          <el-button
            type="primary"
            class="pay-primary"
            :loading="busy"
            @click="confirmSandboxPay"
          >
            我已完成支付
          </el-button>
        </template>

        <template v-else>
          <ol class="pay-steps">
            <li
              v-for="(line, i) in intent.instructions"
              :key="i"
            >
              {{ line }}
            </li>
          </ol>
          <el-form
            label-position="top"
            class="pay-form"
          >
            <el-form-item label="银行回单号">
              <el-input
                v-model="voucherNo"
                placeholder="如 BANK-20261112-0001"
                maxlength="64"
              />
            </el-form-item>
            <el-form-item label="实际转账金额（元）">
              <el-input-number
                v-model="voucherAmount"
                :min="0"
                :precision="2"
                :step="1"
                style="width: 100%"
              />
            </el-form-item>
          </el-form>
          <el-alert
            type="info"
            :closable="false"
            class="pay-hint"
          >
            回单提交后由平台核对真伪，核对通过后额度自动到账；请按实际转账金额填写（默认已填订单金额），金额不一致会标注并转人工核对。
          </el-alert>
          <el-button
            type="primary"
            class="pay-primary"
            :loading="busy"
            :disabled="!voucherNo.trim()"
            @click="doSubmitVoucher"
          >
            提交回单
          </el-button>
        </template>
      </template>
      <template #footer>
        <el-button @click="payOpen = false">稍后再付</el-button>
      </template>
    </el-dialog>

    <!-- 到账权益反馈（付费后立刻告诉用户"你得到了什么"） -->
    <el-dialog
      v-model="settledOpen"
      append-to-body
      title="支付成功，额度已到账"
      width="480px"
    >
      <p class="muted">
        「{{ settledPlan?.name ?? '' }}」的额度已写入本小院，立即生效。
      </p>
      <!-- 新增额度 + 当前剩余 + 建议动作（审查 D-19：付费后的价值感知闭环） -->
      <ul
        v-if="settledPlan"
        class="settled-items"
      >
        <li
          v-for="(e, i) in settledPlan.entitlement ?? []"
          :key="i"
        >
          {{ e.metric_label }}：共 {{ formatAmount(e.total, e.metric) }} {{ e.unit }}
          <span
            v-if="settledUsageMetric(e.metric)"
            class="muted"
          >（当前剩余 {{ formatAmount(settledUsageMetric(e.metric)!, e.metric) }} {{ e.unit }}）</span>
        </li>
      </ul>
      <p
        v-if="settledUsage?.anchor?.calls_remaining"
        class="settled-anchor"
      >
        按近 {{ settledUsage.window_days }} 天平均单次消耗估算，新额度约还能问
        <strong>{{ settledUsage.anchor.calls_remaining.toLocaleString() }}</strong> 次。
      </p>
      <p
        v-if="recommendation?.recommended_name && recommendation.recommended_code !== settledPlan?.code"
        class="settled-next"
      >
        当前消耗下 <strong>{{ recommendation.recommended_name }}</strong> 更从容，
        <el-button
          type="primary"
          link
          @click="settledOpen = false; jumpToPlan(recommendation.recommended_code)"
        >
          看看 {{ recommendation.recommended_name }}
        </el-button>
      </p>
      <template #footer>
        <el-button
          type="primary"
          @click="settledOpen = false"
        >
          知道了
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  /**
   * 套餐与兑换（v3.71）：支付网关前的自助闭环。
   * - 小院侧：看套餐 → 输码兑换（即时到账）或对公下单（管理员确认后到账）；
   * - 平台侧：发码/作废、确认订单到账；到账统一写 quota_grants，本组件只展示结果。
   */
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import QRCode from 'qrcode'
  import {
    billingApi,
    type BillingCode,
    type BillingOrder,
    type BillingPlan,
    type BillingRecommendation,
    type BillingUsage,
    type PaymentIntent,
  } from '@/api/modules'
  import { isNoResponseError } from '@/api/client'
  import { useAuthStore } from '@/stores/auth'

  const auth = useAuthStore()
  const canManage = computed(() => auth.hasPermission('quota:manage'))
  const isPlatformAdmin = computed(() => auth.roles.includes('platform_admin'))

  const plans = ref<BillingPlan[]>([])
  const orders = ref<BillingOrder[]>([])
  const allOrders = ref<BillingOrder[]>([])
  const codes = ref<BillingCode[]>([])
  // --- 用量口径与升级引导（v3.80 审查 D-19/D-20/D-21）---
  const usage = ref<BillingUsage | null>(null)
  const recommendation = ref<BillingRecommendation | null>(null)
  // 到账瞬间的用量快照（刷新后用来展示"当前剩余"）
  const settledUsage = ref<BillingUsage | null>(null)

  const activePlan = ref<BillingPlan | null>(null)
  const redeemOpen = ref(false)
  const redeemCode = ref('')
  const orderOpen = ref(false)
  const orderNote = ref('')
  const busy = ref(false)

  // --- 自助付费闭环（v3.79）---
  const payOpen = ref(false)
  const intent = ref<PaymentIntent | null>(null)
  const qrDataUrl = ref('')
  const voucherNo = ref('')
  const voucherAmount = ref<number | null>(null)
  const payingOrder = ref('')
  const buyingPlan = ref('')
  const settledOpen = ref(false)
  const settledPlan = ref<BillingPlan | null>(null)

  const codePlan = ref('pro')
  const codeCount = ref(10)

  function formatPrice(value: number): string {
    return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  }

  /** 额度数字：Token 用千分位，算力金额保留两位小数 */
  function formatAmount(value: number, metric?: string): string {
    const num = Number(value) || 0
    if (metric === 'cost') {
      return num.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
    }
    return num.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
  }

  /** 滚到推荐档位的套餐卡（升级引导落地） */
  function jumpToPlan(code: string | null | undefined): void {
    if (!code) return
    const el = document.querySelector(`[data-plan="${code}"]`)
    if (el instanceof HTMLElement) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  function planName(code: string): string {
    return plans.value.find(item => item.code === code)?.name ?? code
  }

  function statusLabel(status: string): string {
    const map: Record<string, string> = {
      pending: '待确认',
      paid: '已到账',
      cancelled: '已取消',
    }
    return map[status] ?? status
  }

  function statusType(status: string): 'warning' | 'success' | 'info' {
    if (status === 'paid') return 'success'
    if (status === 'pending') return 'warning'
    return 'info'
  }

  function codeLabel(status: string): string {
    const map: Record<string, string> = {
      unused: '未使用',
      redeemed: '已兑换',
      disabled: '已作废',
      expired: '已过期',
    }
    return map[status] ?? status
  }

  function shortTime(value: string | null): string {
    if (!value) return '—'
    return value.slice(0, 16).replace('T', ' ')
  }

  function openRedeemWith(plan: BillingPlan): void {
    activePlan.value = plan
    redeemCode.value = ''
    redeemOpen.value = true
  }

  function openOrderWith(plan: BillingPlan): void {
    activePlan.value = plan
    orderNote.value = ''
    orderOpen.value = true
  }

  async function refresh(): Promise<void> {
    try {
      plans.value = await billingApi.plans()
      if (canManage.value) orders.value = await billingApi.listOrders('yard')
      if (isPlatformAdmin.value) {
        allOrders.value = await billingApi.listOrders('all')
        codes.value = await billingApi.listCodes()
      }
    } catch {
      /* 读不到就静默：面板不强依赖 */
    }
    // 用量与升级引导独立加载：失败不影响套餐展示
    try {
      usage.value = await billingApi.usage()
    } catch {
      usage.value = null
    }
    try {
      recommendation.value = await billingApi.recommendation()
    } catch {
      recommendation.value = null
    }
  }

  async function doRedeem(): Promise<void> {
    if (!redeemCode.value.trim()) return
    busy.value = true
    try {
      const order = await billingApi.redeem(redeemCode.value.trim().toUpperCase())
      ElMessage.success(`兑换成功，「${planName(order.plan_code)}」额度已到账`)
      redeemOpen.value = false
      await refresh()
      showSettled(order.plan_code)
    } catch {
      /* 错误 toast 由拦截器统一提示 */
    } finally {
      busy.value = false
    }
  }

  // --- 自助付费闭环（v3.79）-----------------------------------------------

  function showSettled(planCode: string): void {
    settledPlan.value = plans.value.find(p => p.code === planCode) ?? null
    // 快照到账后的用量口径：权益卡上展示"当前剩余"用
    settledUsage.value = usage.value
    settledOpen.value = true
  }

  /** 到账后展示的"当前剩余"：按指标取用量口径里的 remaining */
  function settledUsageMetric(metric: string): number | null {
    const found = settledUsage.value?.metrics.find(m => m.metric === metric)
    if (!found || found.remaining === null) return null
    return found.remaining
  }

  /** 打开支付对话框：生成支付意图并按渠道渲染（扫码 / 转账）。 */
  async function openPay(order: BillingOrder): Promise<void> {
    payingOrder.value = order.id
    try {
      const next = await billingApi.createPayment(order.id)
      intent.value = next
      qrDataUrl.value = ''
      voucherNo.value = ''
      voucherAmount.value = order.price_yuan
      payOpen.value = true
      if (next.mode === 'qr' && next.qr_payload) {
        qrDataUrl.value = await QRCode.toDataURL(next.qr_payload, { margin: 1, width: 200 })
      }
    } catch {
      /* 错误 toast 由拦截器统一提示 */
    } finally {
      payingOrder.value = ''
    }
  }

  /** 「立即开通」：下单 → 直接进入支付，一步到底。 */
  async function buyNow(plan: BillingPlan): Promise<void> {
    // 加油包必须附凭证说明（后端口径），走下单弹窗而不是直接支付
    if (plan.kind === 'booster') {
      openOrderWith(plan)
      return
    }
    buyingPlan.value = plan.code
    try {
      const order = await billingApi.createOrder(plan.code)
      await refresh()
      await openPay(order)
    } catch {
      /* 错误 toast 由拦截器统一提示 */
    } finally {
      buyingPlan.value = ''
    }
  }

  /** 沙箱收银台「我已完成支付」：走真实的公开回调端点，验证的是同一条链路。 */
  async function confirmSandboxPay(): Promise<void> {
    if (!intent.value) return
    busy.value = true
    try {
      const { payload, notify_url } = await billingApi.sandboxPrepare(intent.value.order_id)
      await billingApi.sandboxNotify(notify_url, payload)
      ElMessage.success('支付成功，额度已到账')
      payOpen.value = false
      await refresh()
      showSettled(intent.value.plan_code)
    } catch {
      /* 错误 toast 由拦截器统一提示 */
    } finally {
      busy.value = false
    }
  }

  /**
   * 对公转账回单提交：登记回单 + 金额预检。
   * 到账由平台核验回单真伪后「确认到账」（2026-11-13 二次深审修正：
   * 用户自报金额匹配即自动到账是可被伪造回单利用的荣誉制，已移除）。
   */
  async function doSubmitVoucher(): Promise<void> {
    if (!intent.value || !voucherNo.value.trim()) return
    busy.value = true
    try {
      const result = await billingApi.submitVoucher(
        intent.value.order_id,
        voucherNo.value.trim(),
        voucherAmount.value ?? undefined,
      )
      if (result.amount_matched) {
        ElMessage.success(result.message ?? '回单已提交，平台核对通过后额度自动到账')
      } else {
        ElMessage.warning(result.message ?? '回单金额与订单不一致，已标注转平台人工核对')
      }
      payOpen.value = false
      await refresh()
    } catch {
      /* 错误 toast 由拦截器统一提示 */
    } finally {
      busy.value = false
    }
  }

  async function doOrder(): Promise<void> {
    if (!activePlan.value) return
    const plan = activePlan.value
    // 提交前记住已有待确认订单：回查只认「新增」的那一张，避免把旧单当成本次结果
    const pendingBefore = new Set(
      orders.value.filter(o => o.status === 'pending').map(o => o.id),
    )
    busy.value = true
    try {
      await billingApi.createOrder(plan.code, orderNote.value.trim() || undefined)
      ElMessage.success('订单已提交，请在订单列表点「去支付」完成付款')
      orderOpen.value = false
      await refresh()
    } catch (err) {
      // 超时/断网：写操作结果未知，回查订单列表确认，避免用户重复下单（审查 F3）
      if (isNoResponseError(err)) {
        try {
          const latest = await billingApi.listOrders('yard')
          orders.value = latest
          const created = latest.find(
            o => o.plan_code === plan.code && o.status === 'pending' && !pendingBefore.has(o.id),
          )
          if (created) {
            ElMessage.warning(`提交超时，但已核对到订单创建成功（${plan.name}），等待平台确认到账`)
            orderOpen.value = false
          } else {
            ElMessage.warning('提交超时，未确认订单是否创建成功；请先刷新列表确认，再决定是否重试')
          }
        } catch {
          ElMessage.warning('提交超时，且暂时无法核对订单；请稍后刷新列表确认，避免重复下单')
        }
      }
      /* 其它错误 toast 由拦截器统一提示 */
    } finally {
      busy.value = false
    }
  }

  async function cancel(orderId: string): Promise<void> {
    try {
      await billingApi.cancelOrder(orderId)
      ElMessage.success('订单已取消')
      await refresh()
    } catch {
      /* 同上 */
    }
  }

  async function confirm(orderId: string): Promise<void> {
    try {
      await billingApi.confirmOrder(orderId, '对公转账已到账')
      ElMessage.success('已确认到账，额度已写入该小院')
      await refresh()
    } catch {
      /* 同上 */
    }
  }

  async function genCodes(): Promise<void> {
    try {
      const made = await billingApi.createCodes(codePlan.value, codeCount.value)
      codes.value = [...made, ...codes.value]
      const list = made.map(item => item.code).join('\n')
      await navigator.clipboard?.writeText(list).catch(() => undefined)
      ElMessage.success(`已生成 ${made.length} 个兑换码并复制到剪贴板`)
    } catch {
      /* 同上 */
    }
  }

  async function disable(codeId: string): Promise<void> {
    try {
      await billingApi.disableCode(codeId)
      await refresh()
    } catch {
      /* 同上 */
    }
  }

  onMounted(refresh)
</script>

<style scoped>
  .billing {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  .billing-note {
    border-radius: 10px;
  }

  .plan-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(252px, 1fr));
    gap: 12px;
  }

  .plan-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 14px 16px;
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-bg-1);
  }

  .plan-card.booster {
    border-style: dashed;
  }

  .plan-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
  }

  .plan-head strong {
    font-size: 15px;
  }

  .plan-price {
    color: var(--ds-accent);
    font-weight: 700;
  }

  .plan-tagline {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 12px;
    line-height: 1.5;
  }

  .plan-items {
    margin: 0;
    padding-left: 18px;
    color: var(--ds-text-2);
    font-size: 12.5px;
  }

  /* --- 用量水位与升级引导（v3.80 审查 D-19/D-20/D-21）--- */
  .usage-box {
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    background: var(--ds-bg-soft);
  }

  .usage-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    font-size: 13px;
  }

  .usage-warn {
    color: var(--ds-warning);
    font-size: 12.5px;
  }

  .usage-metrics {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .usage-metrics li {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .usage-label {
    font-size: 12px;
    color: var(--ds-text-2);
  }

  .usage-value {
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  /* 水位条轨道:默认浅灰在暗色下几乎不可见,统一走玻璃层 token */
  .usage-metrics :deep(.el-progress-bar__outer) {
    background-color: var(--ds-glass);
  }

  .usage-anchor {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }

  .usage-recommend {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12.5px;
  }

  .usage-recommend-text {
    flex: 1 1 240px;
    color: var(--ds-text-2);
  }

  /* 套餐卡里的额度明细：卖什么一目了然 */
  .plan-quota {
    margin: 0 0 6px;
    padding: 8px 10px;
    list-style: none;
    border-radius: 8px;
    background: var(--ds-bg-soft);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .plan-quota li {
    display: flex;
    align-items: baseline;
    gap: 6px;
    flex-wrap: wrap;
    font-size: 12.5px;
  }

  .plan-quota-label {
    color: var(--ds-text-2);
  }

  .plan-quota-value {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }

  .plan-quota-base {
    color: var(--ds-text-3, #909399);
    font-size: 11.5px;
  }

  .settled-anchor {
    margin: 6px 0 0;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }

  .settled-next {
    margin: 6px 0 0;
    font-size: 12.5px;
  }

  .plan-actions {
    margin-top: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    /* Element 按钮在窄卡里不收缩文字，防止「下单」被裁成半个字 */
    :deep(.el-button) {
      margin-left: 0;
      flex: 0 0 auto;
    }
  }

  .muted {
    color: var(--ds-text-3);
    font-size: 12.5px;
  }

  .orders h4,
  .admin-zone h4 {
    margin: 0 0 8px;
    font-size: 13.5px;
    color: var(--ds-text-1);
  }

  .admin-zone {
    border-top: 1px dashed var(--ds-border);
    padding-top: 14px;
  }

  .admin-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }

  /* --- 支付对话框 --- */
  .pay-summary {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }

  .pay-amount {
    font-size: 22px;
    font-weight: 700;
    color: var(--ds-accent);
  }

  .pay-qr {
    display: flex;
    justify-content: center;
    padding: 6px 0 12px;
  }

  .pay-qr img {
    width: 200px;
    height: 200px;
    border-radius: 10px;
    border: 1px solid var(--ds-border);
    background: #fff;
  }

  .pay-steps {
    margin: 0 0 14px;
    padding-left: 20px;
    color: var(--ds-text-2);
    font-size: 13px;
    line-height: 1.7;
  }

  .pay-form {
    margin-top: 4px;
  }

  .pay-hint {
    margin-bottom: 12px;
    border-radius: 10px;
  }

  .pay-primary {
    width: 100%;
  }

  .settled-items {
    margin: 0;
    padding-left: 20px;
    color: var(--ds-text-2);
    font-size: 13px;
    line-height: 1.8;
  }
</style>
