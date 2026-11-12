<template>
  <div
    class="plan-admin"
    data-plan-admin
  >
    <el-alert
      type="info"
      :closable="false"
      class="admin-note"
    >
      改价、改卖点、改额度、上下架都在这里完成，改动立即对新订单生效；已成交订单保存的是下单时的价格快照，不会被改价波及。
      下架的套餐对用户不可见，但历史订单与兑换码仍可查。
    </el-alert>

    <div
      v-if="loading"
      class="admin-loading"
    >
      正在读取套餐目录…
    </div>

    <el-table
      v-else
      v-col-resize
      :data="plans"
      border
      class="admin-table"
    >
      <el-table-column
        prop="name"
        label="套餐"
        min-width="140"
        show-overflow-tooltip
      />
      <el-table-column
        label="价格"
        width="120"
      >
        <template #default="{ row }">
          ¥{{ formatPrice(row.price_yuan) }}
        </template>
      </el-table-column>
      <el-table-column
        label="类型"
        width="110"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            effect="light"
          >
            {{ row.kind === 'booster' ? '加油包' : '订阅' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="额度项"
        min-width="240"
      >
        <template #default="{ row }">
          <span
            v-for="(it, i) in row.items"
            :key="i"
            class="item-chip"
          >{{ metricLabel(it.metric) }} {{ formatAmount(it.amount) }}{{ unitOf(it.metric) }}</span>
        </template>
      </el-table-column>
      <el-table-column
        label="卖点"
        min-width="220"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          {{ (row.highlights ?? []).join('；') }}
        </template>
      </el-table-column>
      <el-table-column
        label="排序"
        width="90"
      >
        <template #default="{ row }">
          {{ row.sort_order ?? 0 }}
        </template>
      </el-table-column>
      <el-table-column
        label="状态"
        width="140"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.active === false ? 'info' : 'success'"
            effect="light"
          >
            {{ row.active === false ? '已下架' : '在售' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="200"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            link
            @click="openEdit(row as BillingPlan)"
          >
            编辑
          </el-button>
          <el-button
            size="small"
            type="info"
            link
            @click="toggleActive(row as BillingPlan)"
          >
            {{ row.active === false ? '重新上架' : '下架' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="editOpen"
      append-to-body
      align-center
      draggable
      width="min(720px, 94vw)"
      title="编辑套餐"
    >
      <div
        v-if="editing"
        class="edit-form"
      >
        <el-form
          label-width="110px"
          label-position="right"
        >
          <el-form-item label="套餐名称">
            <strong>{{ editing.name }}</strong>
            <span class="muted">（{{ editing.code }}）</span>
          </el-form-item>
          <el-form-item label="价格（元）">
            <el-input-number
              v-model="form.price_yuan"
              :min="0.01"
              :max="1000000"
              :step="100"
              :precision="2"
            />
          </el-form-item>
          <el-form-item label="一句话卖点">
            <el-input
              v-model="form.tagline"
              maxlength="200"
              show-word-limit
              placeholder="如：单小院起步：日常对话、知识库与工单闭环"
            />
          </el-form-item>
          <el-form-item label="卖点条目">
            <el-input
              v-model="form.highlightsText"
              type="textarea"
              :rows="5"
              placeholder="一行一条，最多 8 条；用于付费页的对比表"
            />
          </el-form-item>
          <el-form-item label="额度项">
            <div class="items-editor">
              <div
                v-for="(it, i) in form.items"
                :key="i"
                class="item-row"
              >
                <el-select
                  v-model="it.metric"
                  class="item-metric"
                >
                  <el-option
                    v-for="m in METRICS"
                    :key="m.value"
                    :label="m.label"
                    :value="m.value"
                  />
                </el-select>
                <el-input-number
                  v-model="it.amount"
                  :min="0"
                  :step="10000"
                  :controls="false"
                  class="item-amount"
                />
                <el-input-number
                  v-model="it.valid_days"
                  :min="0"
                  :max="3650"
                  :controls="false"
                  class="item-days"
                />
                <el-input
                  v-model="it.note"
                  class="item-note"
                  maxlength="120"
                  placeholder="备注（选填）"
                />
                <el-button
                  type="danger"
                  link
                  @click="removeItem(i)"
                >
                  删除
                </el-button>
              </div>
              <el-button
                size="small"
                @click="addItem"
              >
                + 增加额度项
              </el-button>
            </div>
          </el-form-item>
          <el-form-item label="排序">
            <el-input-number
              v-model="form.sort_order"
              :min="0"
              :max="10000"
            />
            <span class="muted">数值小的在付费页靠前</span>
          </el-form-item>
          <el-form-item label="在售">
            <el-switch
              v-model="form.active"
            />
            <span class="muted">下架后用户看不到该套餐，历史订单不受影响</span>
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="editOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="save"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  /**
   * 运营套餐管理（v3.80，审查 D-18）。
   *
   * 审查原文：「套餐硬编码、额度不明、标价话术自相矛盾」——修完口径之后，
   * 剩下的最后一环是**运营能自己改**：改价、改卖点、改额度、上下架不用发版。
   * 后端 ``GET/PATCH /billing/admin/plans``（平台管理员）已就绪，这里补前端。
   */
  import { onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { billingApi, type BillingPlan, type BillingPlanUpdate } from '@/api/modules'

  const METRICS = [
    { value: 'tokens', label: 'Token', unit: '' },
    { value: 'calls', label: '调用次数', unit: ' 次' },
    { value: 'cost', label: '模型算力', unit: ' 元' },
  ]

  interface EditItem {
    metric: string
    amount: number
    valid_days: number
    note: string
  }

  const plans = ref<BillingPlan[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const editOpen = ref(false)
  const editing = ref<BillingPlan | null>(null)

  const form = ref<{
    price_yuan: number
    tagline: string
    highlightsText: string
    items: EditItem[]
    sort_order: number
    active: boolean
  }>({
    price_yuan: 0,
    tagline: '',
    highlightsText: '',
    items: [],
    sort_order: 0,
    active: true,
  })

  function metricLabel(metric: string): string {    return METRICS.find(m => m.value === metric)?.label ?? metric
  }

  function unitOf(metric: string): string {
    return METRICS.find(m => m.value === metric)?.unit ?? ''
  }

  function formatPrice(value: number): string {
    return Number(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }

  function formatAmount(value: number): string {
    return Number(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }

  async function load(): Promise<void> {
    loading.value = true
    try {
      plans.value = await billingApi.adminPlans()
    } catch {
      plans.value = []
    } finally {
      loading.value = false
    }
  }

  function openEdit(plan: BillingPlan): void {
    editing.value = plan
    form.value = {
      price_yuan: Number(plan.price_yuan ?? 0),
      tagline: plan.tagline ?? '',
      highlightsText: (plan.highlights ?? []).join('\n'),
      items: (plan.items ?? []).map(it => ({
        metric: it.metric,
        amount: Number(it.amount ?? 0),
        valid_days: Number(it.valid_days ?? 0),
        note: it.note ?? '',
      })),
      sort_order: Number(plan.sort_order ?? 0),
      active: plan.active !== false,
    }
    editOpen.value = true
  }

  function addItem(): void {
    form.value.items.push({ metric: 'tokens', amount: 1000000, valid_days: 0, note: '' })
  }

  function removeItem(index: number): void {
    form.value.items.splice(index, 1)
  }

  async function save(): Promise<void> {
    const plan = editing.value
    if (!plan) return
    // 套餐是付费 SKU：0/负价会被后端拒绝（免费发放走兑换码），前端同口径前置拦截
    const price = Number(form.value.price_yuan ?? 0)
    if (!(price > 0)) {
      ElMessage.warning('套餐价格必须大于 0；免费发放请走兑换码，不要上架 0 元套餐')
      return
    }
    const highlights = form.value.highlightsText
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)
      .slice(0, 8)
    const payload: BillingPlanUpdate = {
      price_yuan: price,
      tagline: form.value.tagline.trim(),
      highlights,
      items: form.value.items.map(it => ({
        metric: it.metric,
        window: 'month',
        amount: Number(it.amount ?? 0),
        valid_days: Number(it.valid_days ?? 0),
        note: it.note ?? '',
      })),
      sort_order: Number(form.value.sort_order ?? 0),
      active: form.value.active,
    }
    saving.value = true
    try {
      await billingApi.updatePlan(plan.code, payload)
      ElMessage.success('套餐已更新，对新订单立即生效')
      editOpen.value = false
      await load()
    } catch {
      ElMessage.error('套餐更新失败，请检查额度项是否合法')
    } finally {
      saving.value = false
    }
  }

  async function toggleActive(plan: BillingPlan): Promise<void> {
    try {
      await billingApi.updatePlan(plan.code, { active: plan.active === false })
      ElMessage.success(plan.active === false ? '已重新上架' : '已下架')
      await load()
    } catch {
      ElMessage.error('操作失败')
    }
  }

  onMounted(load)
</script>

<style scoped>
  .plan-admin { display: flex; flex-direction: column; gap: 12px; }
  .admin-note { margin: 0; }
  .admin-loading { padding: 18px; font-size: 13px; color: var(--ds-text-3); }
  .admin-table { width: 100%; overflow-x: auto; }
  .item-chip { display: inline-block; margin: 2px 6px 2px 0; padding: 1px 8px; border: 1px solid var(--ds-border); border-radius: 999px; font-size: 11px; color: var(--ds-text-2); }
  .edit-form { max-height: min(62vh, 560px); overflow: auto; padding-right: 4px; }
  .items-editor { display: flex; flex-direction: column; gap: 8px; width: 100%; }
  .item-row { display: grid; grid-template-columns: 130px 130px 100px 1fr auto; gap: 8px; align-items: center; }
  .item-metric { width: 130px; }
  .item-amount { width: 130px; }
  .item-days { width: 100px; }
  .muted { margin-left: 8px; font-size: 12px; color: var(--ds-text-3); }
  @media (max-width: 720px) {
    .item-row { grid-template-columns: 1fr 1fr; }
    .item-note { grid-column: span 2; }
  }
</style>
