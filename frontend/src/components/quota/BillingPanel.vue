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
      支付网关接入前的自助闭环：平台管理员线下收款后发「兑换码」，在这里兑换即时到账；
      也可以选套餐下「对公转账订单」，平台管理员确认到账后同样自动写入额度。定价为示例价，签约以商务报价为准。
    </el-alert>

    <!-- 套餐目录 -->
    <div class="plan-grid">
      <div
        v-for="plan in plans"
        :key="plan.code"
        class="plan-card"
        :class="{ booster: plan.kind === 'booster' }"
      >
        <div class="plan-head">
          <strong>{{ plan.name }}</strong>
          <span class="plan-price">¥{{ formatPrice(plan.price_yuan) }}</span>
        </div>
        <p class="plan-tagline">
          {{ plan.tagline }}
        </p>
        <ul class="plan-items">
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
            对公下单
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
          width="90"
        >
          <template #default="{ row }">
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
            prop="voucher_note"
            label="凭证说明"
            min-width="180"
            show-overflow-tooltip
          />
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
      :title="`对公转账下单 · ${activePlan?.name ?? ''}`"
      width="460px"
    >
      <p class="muted">
        应付金额：¥{{ activePlan ? formatPrice(activePlan.price_yuan) : '-' }}。转账后填写回单号/银行流水，
        平台管理员确认到账后额度自动写入。
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
  import {
    billingApi,
    type BillingCode,
    type BillingOrder,
    type BillingPlan,
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

  const activePlan = ref<BillingPlan | null>(null)
  const redeemOpen = ref(false)
  const redeemCode = ref('')
  const orderOpen = ref(false)
  const orderNote = ref('')
  const busy = ref(false)

  const codePlan = ref('pro')
  const codeCount = ref(10)

  function formatPrice(value: number): string {
    return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
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
  }

  async function doRedeem(): Promise<void> {
    if (!redeemCode.value.trim()) return
    busy.value = true
    try {
      const order = await billingApi.redeem(redeemCode.value.trim().toUpperCase())
      ElMessage.success(`兑换成功，「${planName(order.plan_code)}」额度已到账`)
      redeemOpen.value = false
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
      ElMessage.success('订单已提交，等待平台管理员确认到账')
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
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
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

  .plan-actions {
    margin-top: auto;
    display: flex;
    gap: 8px;
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
</style>
