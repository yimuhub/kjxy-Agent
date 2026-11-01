<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>官方模型</span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="load()"
          >
            刷新
          </el-button>
        </div>
      </div>
    </template>

    <AsyncState
      :state="state"
      empty-text="官方通道尚未开通"
      @retry="load()"
    >
      <template #empty-action>
        <div class="empty-desc">
          <p>官方模型通道预留中，开通后可试用/购买。</p>
          <p
            v-if="message"
            class="note"
          >
            {{ message }}
          </p>
          <el-button
            size="small"
            @click="load()"
          >
            重新检查
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="status.message"
        type="info"
        :title="status.message"
        :closable="false"
        show-icon
        class="hint"
      />
      <el-alert
        v-if="missingSections.length"
        type="warning"
        :title="`部分官方接口暂未就绪：${missingSections.join('、')}，已展示可用部分。`"
        :closable="false"
        show-icon
        class="hint"
      />

      <div class="sub-head">
        <h4>套餐</h4>
        <span class="note">试用与购买均以服务端返回的权益/订单为准</span>
      </div>

      <el-empty
        v-if="!plans.length"
        description="暂无可用套餐"
        :image-size="72"
      />
      <div
        v-else
        class="plan-grid"
      >
        <el-card
          v-for="(plan, index) in plans"
          :key="plan.code || `${plan.model_key || 'plan'}-${index}`"
          class="plan-card"
          shadow="never"
        >
          <div class="plan-head">
            <span class="plan-name">{{ plan.name || plan.code || '未命名套餐' }}</span>
            <ModalityTag :modality="modelModality(plan.model_key)" />
          </div>
          <div class="plan-model">
            {{ modelLabel(plan.model_key) }}
            <span
              v-if="plan.model_key"
              class="note"
            >{{ plan.model_key }}</span>
          </div>
          <div class="plan-price">
            {{ money(plan.price, plan.currency) }}
            <span class="plan-unit">{{ unitLabel(plan.billing_unit) }}</span>
          </div>
          <div class="plan-trial">
            {{ trialText(plan) }}
          </div>
          <div class="plan-actions">
            <el-tooltip
              :disabled="trialAvailable(plan)"
              content="该套餐未配置试用额度"
              placement="top"
            >
              <span>
                <el-button
                  size="small"
                  :disabled="!trialAvailable(plan) || !plan.model_key || !plan.code"
                  :loading="trialKey === planKey(plan)"
                  @click="startTrial(plan)"
                >
                  试用
                </el-button>
              </span>
            </el-tooltip>
            <el-button
              size="small"
              type="primary"
              :disabled="!plan.model_key || !plan.code"
              :loading="buyKey === planKey(plan)"
              @click="buy(plan)"
            >
              购买
            </el-button>
          </div>
        </el-card>
      </div>

      <div class="sub-head">
        <h4>我的权益</h4>
        <span class="note">共 {{ entitlements.length }} 条</span>
      </div>
      <div class="table-scroll">
        <el-table
          v-col-resize
          :data="entitlements"
          row-key="id"
          stripe
          empty-text="暂无权益，先试用或购买套餐"
        >
          <el-table-column
            label="模型"
            min-width="200"
          >
            <template #default="{ row }">
              <div class="ent-model">
                <span>{{ modelLabel((row as OfficialEntitlement).model_key) }}</span>
                <span class="note">{{ (row as OfficialEntitlement).model_key || '—' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="来源"
            width="120"
          >
            <template #default="{ row }">
              {{ sourceLabel((row as OfficialEntitlement).source) }}
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag
                :type="entitlementMeta((row as OfficialEntitlement).status).type"
                size="small"
                effect="plain"
              >
                {{ entitlementMeta((row as OfficialEntitlement).status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="额度"
            width="140"
          >
            <template #default="{ row }">
              {{ quotaText(row as OfficialEntitlement) }}
            </template>
          </el-table-column>
          <el-table-column
            label="有效期"
            min-width="230"
          >
            <template #default="{ row }">
              {{ validityText(row as OfficialEntitlement) }}
            </template>
          </el-table-column>
        </el-table>
      </div>
    </AsyncState>

    <!-- 购买订单：只展示占位，不假装支付成功 -->
    <el-dialog
      v-model="orderVisible"
      append-to-body
      title="订单已创建"
      width="580px"
    >
      <template v-if="order">
        <el-descriptions
          :column="2"
          border
        >
          <el-descriptions-item label="订单号">
            {{ order.id || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            {{ orderStatusLabel(order.status) }}
          </el-descriptions-item>
          <el-descriptions-item label="金额">
            {{ money(order.amount, order.currency) }}
          </el-descriptions-item>
          <el-descriptions-item label="支付">
            {{ order.payment_url ? '待打开支付页面' : '暂无支付链接' }}
          </el-descriptions-item>
        </el-descriptions>
        <div
          v-if="order.payment_url"
          class="pay-row"
        >
          <el-link
            type="primary"
            :href="order.payment_url"
            target="_blank"
            rel="noopener noreferrer"
          >
            打开支付页面
          </el-link>
        </div>
        <el-alert
          v-else
          type="info"
          :closable="false"
          show-icon
          class="hint"
          title="服务端未返回支付链接"
          description="本版本只展示订单占位，不会自动完成支付，也不会把订单标记为已支付。"
        />
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="hint"
          title="支付状态以服务端订单为准，请保留订单号以便查询。"
        />
      </template>
      <template #footer>
        <el-button
          type="primary"
          @click="orderVisible = false"
        >
          关闭
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  /** 官方模型通道：套餐、试用、购买（占位）与权益。未开通时展示空态而非红色错误。 */
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import { isOfficialDisabled, officialApi } from '@/api/modelChannels'
  import type {
    OfficialEntitlement,
    OfficialModel,
    OfficialOrder,
    OfficialPlan,
    OfficialStatus,
    TagType,
  } from '@/api/modelChannels'
  import AsyncState from '@/components/AsyncState.vue'
  import ModalityTag from '@/components/settings/ModalityTag.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import { formatTime } from '@/utils/format'

  const state = ref<ViewState>('loading')
  const status = ref<OfficialStatus>({})
  const models = ref<OfficialModel[]>([])
  const plans = ref<OfficialPlan[]>([])
  const entitlements = ref<OfficialEntitlement[]>([])
  const trialKey = ref('')
  const buyKey = ref('')
  const order = ref<OfficialOrder | null>(null)
  const orderVisible = ref(false)
  const missingSections = ref<string[]>([])

  const message = computed(() => status.value.message || '')

  async function load(): Promise<void> {
    state.value = 'loading'
    missingSections.value = []
    try {
      const res = await officialApi.status()
      status.value = res
      if (res.enabled !== true) {
        state.value = 'empty'
        return
      }
      const [modelRes, planRes, entitlementRes] = await Promise.allSettled([
        officialApi.models(),
        officialApi.plans(),
        officialApi.entitlements(),
      ])
      const results = [modelRes, planRes, entitlementRes]
      const failures = results.filter(
        (item): item is PromiseRejectedResult => item.status === 'rejected',
      )
      if (failures.length === results.length) {
        const disabled = failures.find(item => isOfficialDisabled(item.reason))
        if (disabled) {
          status.value = { enabled: false, message: extractErrorMessage(disabled.reason).message }
          state.value = 'empty'
          return
        }
        state.value = 'error'
        return
      }
      models.value = modelRes.status === 'fulfilled' ? modelRes.value : []
      plans.value = planRes.status === 'fulfilled' ? planRes.value : []
      entitlements.value = entitlementRes.status === 'fulfilled' ? entitlementRes.value : []
      missingSections.value = [
        modelRes.status === 'rejected' ? '模型' : '',
        planRes.status === 'rejected' ? '套餐' : '',
        entitlementRes.status === 'rejected' ? '权益' : '',
      ].filter(Boolean)
      state.value = 'ready'
    } catch (e) {
      if (isOfficialDisabled(e)) {
        status.value = { enabled: false, message: extractErrorMessage(e).message }
        state.value = 'empty'
        return
      }
      state.value = classifyError(e)
    }
  }

  function planKey(plan: OfficialPlan): string {
    return `${plan.model_key || ''}:${plan.code || ''}`
  }

  function modelFor(modelKey?: string | null): OfficialModel | undefined {
    if (!modelKey) return undefined
    return models.value.find(model => model.model_key === modelKey)
  }

  function modelLabel(modelKey?: string | null): string {
    return modelFor(modelKey)?.display_name || modelKey || '未关联模型'
  }

  function modelModality(modelKey?: string | null): string {
    return modelFor(modelKey)?.modality || ''
  }

  function money(value?: number | null, currency?: string | null): string {
    if (value === undefined || value === null) return '—'
    const symbol = currency === 'USD' ? '$' : !currency || currency === 'CNY' ? '¥' : `${currency} `
    return `${symbol}${value}`
  }

  function unitLabel(unit?: string | null): string {
    if (!unit) return ''
    const map: Record<string, string> = {
      per_1k_tokens: '/ 千 token',
      per_1m_tokens: '/ 百万 token',
      per_token: '/ token',
      per_call: '/ 次',
      monthly: '/ 月',
      yearly: '/ 年',
    }
    return map[unit] ?? `/ ${unit}`
  }

  function trialText(plan: OfficialPlan): string {
    const parts: string[] = []
    if (plan.trial_tokens) parts.push(`${plan.trial_tokens.toLocaleString('zh-CN')} tokens`)
    if (plan.trial_days) parts.push(`${plan.trial_days} 天`)
    return parts.length ? `试用：${parts.join(' + ')}` : '未配置试用额度'
  }

  function trialAvailable(plan: OfficialPlan): boolean {
    const model = modelFor(plan.model_key)
    if (model?.trial_available === false) return false
    if (model?.trial_available === true) return true
    return (plan.trial_tokens ?? 0) > 0 || (plan.trial_days ?? 0) > 0
  }

  function sourceLabel(source?: string | null): string {
    if (!source) return '—'
    const map: Record<string, string> = {
      trial: '试用',
      order: '购买',
      purchase: '购买',
      paid: '购买',
      admin: '管理员发放',
      grant: '管理员发放',
    }
    return map[source] ?? source
  }

  function entitlementMeta(statusValue?: string | null): { label: string; type: TagType } {
    const key = String(statusValue ?? '').toLowerCase()
    if (['active', 'valid', 'enabled', 'ok'].includes(key)) {
      return { label: '生效中', type: 'success' }
    }
    if (key === 'trial') return { label: '试用中', type: 'primary' }
    if (['expired', 'ended'].includes(key)) return { label: '已过期', type: 'info' }
    if (['exhausted', 'used', 'used_up', 'depleted'].includes(key)) {
      return { label: '已用完', type: 'warning' }
    }
    if (['revoked', 'cancelled', 'canceled'].includes(key)) {
      return { label: '已撤销', type: 'danger' }
    }
    return { label: statusValue || '未知', type: 'info' }
  }

  function quotaText(item: OfficialEntitlement): string {
    const total = item.quota_total
    const used = item.quota_used
    if (total === undefined || total === null) {
      return used === undefined || used === null ? '—' : `已用 ${used}`
    }
    return `${used ?? 0} / ${total}`
  }

  function validityText(item: OfficialEntitlement): string {
    if (!item.starts_at && !item.expires_at) return '—'
    return `${formatTime(item.starts_at)} ~ ${formatTime(item.expires_at)}`
  }

  function orderStatusLabel(value?: string | null): string {
    if (!value) return '—'
    const map: Record<string, string> = {
      created: '待支付',
      pending: '待支付',
      unpaid: '待支付',
      paid: '已支付',
      cancelled: '已取消',
      canceled: '已取消',
      refunded: '已退款',
    }
    return map[value] ?? value
  }

  async function startTrial(plan: OfficialPlan): Promise<void> {
    if (!plan.model_key || !plan.code) return
    try {
      await ElMessageBox.confirm(
        `确认申请「${plan.name || modelLabel(plan.model_key)}」试用？试用额度与时长以服务端配置为准。`,
        '申请试用',
        { type: 'info' },
      )
    } catch {
      return
    }
    trialKey.value = planKey(plan)
    try {
      await officialApi.startTrial(plan.model_key, plan.code)
      ElMessage.success('试用已申请，权益列表已更新')
      try {
        entitlements.value = await officialApi.entitlements()
      } catch {
        /* 试用已成功，权益刷新失败时保留旧列表，用户可手动刷新 */
      }
    } catch (e) {
      if (isOfficialDisabled(e)) {
        ElMessage.warning('官方通道尚未开通，暂不能试用')
        state.value = 'empty'
        return
      }
      ElMessage.error(`试用申请失败：${extractErrorMessage(e).message}`)
    } finally {
      trialKey.value = ''
    }
  }

  async function buy(plan: OfficialPlan): Promise<void> {
    if (!plan.model_key || !plan.code) return
    try {
      await ElMessageBox.confirm(
        `确认购买「${plan.name || modelLabel(plan.model_key)}」？将生成订单并返回支付链接。`,
        '购买套餐',
        { type: 'info' },
      )
    } catch {
      return
    }
    buyKey.value = planKey(plan)
    try {
      const res = await officialApi.createOrder(plan.model_key, plan.code)
      order.value = res
      orderVisible.value = true
    } catch (e) {
      if (isOfficialDisabled(e)) {
        ElMessage.warning('官方通道尚未开通，暂不能购买')
        state.value = 'empty'
        return
      }
      ElMessage.error(`创建订单失败：${extractErrorMessage(e).message}`)
    } finally {
      buyKey.value = ''
    }
  }

  onMounted(() => {
    void load()
  })
</script>

<style scoped>
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .hint {
    margin-bottom: 12px;
  }
  .empty-desc {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    color: var(--ds-text-2);
    font-size: 13px;
  }
  .empty-desc p {
    margin: 0;
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .sub-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
    margin: 18px 0 10px;
  }
  .sub-head h4 {
    margin: 0;
    font-size: 14px;
    color: var(--ds-text-1);
  }
  .plan-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 12px;
  }
  .plan-card {
    border: 1px solid var(--ds-border);
    background: var(--ds-glass);
  }
  .plan-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .plan-name {
    font-weight: 600;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .plan-model {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 6px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .plan-price {
    margin-top: 12px;
    font-size: 20px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .plan-unit {
    font-size: 12px;
    font-weight: 400;
    color: var(--ds-text-3);
  }
  .plan-trial {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .plan-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 12px;
  }
  .table-scroll {
    overflow-x: auto;
  }
  .ent-model {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .pay-row {
    margin-top: 12px;
  }
</style>
