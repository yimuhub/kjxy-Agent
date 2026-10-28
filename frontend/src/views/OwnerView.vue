<template>
  <div class="owner-view">
    <el-result
      v-if="!isOwner"
      icon="warning"
      title="403 无权限访问"
      sub-title="负责人驾驶舱仅对当前小院负责人开放。如需查看，请联系小院负责人或平台管理员。"
    >
      <template #extra>
        <el-button
          type="primary"
          @click="router.push('/')"
        >
          返回工作台
        </el-button>
      </template>
    </el-result>

    <template v-else>
      <div class="page-head">
        <div class="head-main">
          <h2 class="page-title">
            小院负责人驾驶舱
          </h2>
          <p class="page-sub">
            {{ yardName }} · 数据截至 {{ generatedAt }}
          </p>
        </div>
        <div class="page-actions">
          <el-button
            :loading="refreshing"
            @click="refreshAll"
          >
            刷新
          </el-button>
        </div>
      </div>

      <AsyncState
        :state="overviewState"
        empty-text="暂无运营数据"
        @retry="loadOverview"
      >
        <div class="stat-grid">
          <div
            v-for="card in statCards"
            :key="card.label"
            class="stat-card glass-panel"
          >
            <div class="stat-label">
              {{ card.label }}
            </div>
            <div class="stat-value">
              {{ card.value }}
            </div>
            <div class="stat-hint">
              {{ card.hint }}
            </div>
          </div>
        </div>

        <div class="notice-stack">
          <el-alert
            v-if="failedToday > 0"
            type="error"
            :closable="false"
            show-icon
            :title="`今日有 ${failedToday} 条消息失败，请关注模型服务与网络状态。`"
          />
          <el-alert
            v-if="networkNotice"
            :type="networkNotice.type"
            :closable="false"
            show-icon
            :title="networkNotice.title"
            :description="networkNotice.description"
          />
        </div>
      </AsyncState>

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <div class="header-left">
              <span>成员用量（近 7 天）</span>
              <el-tag
                v-if="sensitive"
                size="small"
                type="success"
                effect="plain"
              >
                含敏感字段
              </el-tag>
              <el-tag
                v-else
                size="small"
                type="info"
                effect="plain"
              >
                联系方式已脱敏
              </el-tag>
            </div>
            <el-button
              size="small"
              @click="loadUsage"
            >
              刷新
            </el-button>
          </div>
        </template>
        <div
          v-if="usageSummaryText"
          class="summary-bar"
        >
          {{ usageSummaryText }}
        </div>
        <AsyncState
          :state="usageState"
          empty-text="近 7 天暂无成员用量"
          @retry="loadUsage"
        >
          <el-table
            :data="sortedUsage"
            stripe
          >
            <el-table-column
              label="成员"
              min-width="160"
            >
              <template #default="{ row }">
                <div class="member-cell">
                  <span class="member-name">{{ row.display_name || row.username }}</span>
                  <span class="member-sub">@{{ row.username }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              label="角色"
              width="120"
            >
              <template #default="{ row }">
                {{ roleLabel(row.role) }}
              </template>
            </el-table-column>
            <el-table-column
              label="联系方式"
              min-width="180"
            >
              <template #default="{ row }">
                <span class="contact-text">{{ contactOf(row as MemberUsageRow) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="sessions"
              label="会话"
              width="80"
            />
            <el-table-column
              prop="messages"
              label="消息"
              width="80"
            />
            <el-table-column
              label="输入"
              width="100"
            >
              <template #default="{ row }">
                {{ formatNumber(row.input_tokens) }}
              </template>
            </el-table-column>
            <el-table-column
              label="输出"
              width="100"
            >
              <template #default="{ row }">
                {{ formatNumber(row.output_tokens) }}
              </template>
            </el-table-column>
            <el-table-column
              label="总 Token"
              width="110"
            >
              <template #default="{ row }">
                <strong>{{ formatNumber(row.total_tokens) }}</strong>
              </template>
            </el-table-column>
            <el-table-column
              label="最近活跃"
              width="170"
            >
              <template #default="{ row }">
                {{ formatTime(row.last_active) }}
              </template>
            </el-table-column>
          </el-table>
        </AsyncState>
      </el-card>

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>近 7 天趋势</span>
            <el-button
              size="small"
              @click="loadDaily"
            >
              刷新
            </el-button>
          </div>
        </template>
        <AsyncState
          :state="dailyState"
          empty-text="近 7 天暂无趋势数据"
          @retry="loadDaily"
        >
          <UsageTrend :items="dailyItems" />
        </AsyncState>
      </el-card>

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>Agent 运营（近 7 天）</span>
            <el-button
              size="small"
              @click="loadAgents"
            >
              刷新
            </el-button>
          </div>
        </template>
        <AsyncState
          :state="agentState"
          empty-text="近 7 天暂无 Agent 调用记录"
          @retry="loadAgents"
        >
          <el-table
            :data="agentRows"
            stripe
          >
            <el-table-column
              label="Agent"
              min-width="200"
            >
              <template #default="{ row }">
                <div class="member-cell">
                  <span class="member-name">{{ row.name }}</span>
                  <span class="member-sub">{{ row.code }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column
              prop="calls"
              label="调用"
              width="100"
            />
            <el-table-column
              label="失败"
              width="100"
            >
              <template #default="{ row }">
                <el-tag
                  v-if="row.failed > 0"
                  size="small"
                  type="danger"
                >
                  {{ row.failed }}
                </el-tag>
                <span v-else>0</span>
              </template>
            </el-table-column>
            <el-table-column
              label="Token"
              width="120"
            >
              <template #default="{ row }">
                {{ formatNumber(row.total_tokens) }}
              </template>
            </el-table-column>
          </el-table>
        </AsyncState>
      </el-card>

      <SessionAudit :members="memberUsageItems" />

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>授权中心</span>
            <el-button
              size="small"
              @click="loadGrants"
            >
              刷新
            </el-button>
          </div>
        </template>
        <p class="section-tip">
          管理员默认看不到敏感数据。授权后管理员查看会话正文仍需填写原因并留审计。
        </p>
        <AsyncState
          :state="grantState"
          empty-text="暂无小院管理员，邀请或设置为管理员后可逐项授权"
          @retry="loadGrants"
        >
          <GrantMatrix
            :items="grantItems"
            :saving-id="savingGrantId"
            @save="saveGrants"
          />
        </AsyncState>
      </el-card>

      <el-card class="section danger-section">
        <template #header>
          负责人转让
        </template>
        <TransferOwner
          :admins="grantItems"
          @transferred="onTransferred"
        />
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import {
    ownerApi,
    type AgentUsageRow,
    type DailyUsageRow,
    type GrantRow,
    type MemberUsageRow,
    type OwnerOverview,
  } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import GrantMatrix from '@/components/settings/GrantMatrix.vue'
  import { OFFLINE_SEARCH_TIP, ROLE_LABELS } from '@/components/settings/permissions'
  import SessionAudit from '@/components/owner/SessionAudit.vue'
  import TransferOwner from '@/components/owner/TransferOwner.vue'
  import UsageTrend from '@/components/owner/UsageTrend.vue'
  import { useAuthStore } from '@/stores/auth'
  import { formatTime } from '@/utils/format'
  import { classifyError, type ViewState } from '@/utils/status'

  interface AgentRow {
    code: string
    name: string
    calls: number
    failed: number
    total_tokens: number
  }

  const router = useRouter()
  const auth = useAuthStore()

  const isOwner = computed(
    () => auth.roles.includes('yard_owner') || auth.roles.includes('platform_admin'),
  )
  const yardName = computed(() => auth.yard?.name ?? '当前小院')
  const generatedAt = computed(() => formatTime(overview.value?.generated_at))

  const overview = ref<OwnerOverview | null>(null)
  const overviewState = ref<ViewState>('loading')

  const memberUsageItems = ref<MemberUsageRow[]>([])
  const usageSummary = ref<Record<string, number>>({})
  const sensitive = ref(false)
  const usageState = ref<ViewState>('loading')

  const dailyItems = ref<DailyUsageRow[]>([])
  const dailyState = ref<ViewState>('loading')

  const agentItems = ref<AgentUsageRow[]>([])
  const agentState = ref<ViewState>('loading')

  const grantItems = ref<GrantRow[]>([])
  const grantState = ref<ViewState>('loading')
  const savingGrantId = ref('')

  const refreshing = ref(false)

  function formatNumber(value?: number | null): string {
    return new Intl.NumberFormat('zh-CN').format(Number(value ?? 0))
  }

  function roleLabel(role?: string): string {
    if (!role) return '—'
    return ROLE_LABELS[role] ?? role
  }

  function contactOf(row: MemberUsageRow): string {
    const parts = [row.email, row.phone].filter(Boolean)
    return parts.length ? parts.join(' / ') : '—'
  }

  const sortedUsage = computed(() =>
    [...memberUsageItems.value].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0)),
  )

  const usageSummaryText = computed(() => {
    if (!memberUsageItems.value.length) return ''
    const summary = usageSummary.value
    const parts = [
      `成员 ${formatNumber(summary.members_total ?? memberUsageItems.value.length)}`,
      `会话 ${formatNumber(summary.sessions)}`,
      `消息 ${formatNumber(summary.messages)}`,
      `输入 ${formatNumber(summary.input_tokens)}`,
      `输出 ${formatNumber(summary.output_tokens)}`,
      `总 Token ${formatNumber(summary.total_tokens)}`,
    ]
    return parts.join(' · ')
  })

  const failedToday = computed(() => overview.value?.failed_messages_today ?? 0)

  const statCards = computed(() => {
    const data = overview.value
    const members = data?.members_total ?? 0
    const active = data?.active_today ?? 0
    const pending = data?.pending_reviews
    return [
      { label: '成员总数', value: data ? formatNumber(members) : '—', hint: '在职成员' },
      {
        label: '今日活跃',
        value: data ? formatNumber(active) : '—',
        hint: members ? `活跃率 ${Math.round((active / members) * 100)}%` : '暂无成员',
      },
      {
        label: 'Agent 数',
        value: data ? formatNumber(data.agents_count) : '—',
        hint: '小院内智能体',
      },
      {
        label: '今日会话',
        value: data ? formatNumber(data.sessions_today) : '—',
        hint: '新建会话数',
      },
      {
        label: '今日消息',
        value: data ? formatNumber(data.messages_today) : '—',
        hint: '用户与助手消息合计',
      },
      {
        label: '今日 Token',
        value: data ? formatNumber(data.tokens_today) : '—',
        hint: data
          ? `输入 ${formatNumber(data.tokens_today_input)} / 输出 ${formatNumber(data.tokens_today_output)}`
          : '—',
      },
      {
        label: '7 日 Token',
        value: data ? formatNumber(data.tokens_7d) : '—',
        hint: data
          ? `输入 ${formatNumber(data.tokens_7d_input)} / 输出 ${formatNumber(data.tokens_7d_output)}`
          : '—',
      },
      {
        label: '待审事项',
        value: data ? formatNumber(pending?.total) : '—',
        hint: pending
          ? `知识 ${pending.knowledge_candidates} · 技能 ${pending.skills} · 插件 ${pending.plugins}`
          : '—',
      },
      {
        label: '回答引用覆盖率',
        value:
          data?.trust?.citation_coverage_7d != null
            ? `${Math.round(data.trust.citation_coverage_7d * 100)}%`
            : '—',
        hint: data?.trust
          ? `近 7 日 ${data.trust.cited_messages_7d}/${data.trust.assistant_messages_7d} 条回答带依据`
          : '暂无回答样本',
      },
      {
        label: '7 日模型成本',
        value: data?.trust ? data.trust.llm_cost_7d.toFixed(2) : '—',
        hint: data?.trust
          ? `调用 ${data.trust.llm_calls_7d} 次${
              data.trust.llm_success_rate_7d != null
                ? ` · 成功率 ${Math.round(data.trust.llm_success_rate_7d * 100)}%`
                : ''
            }`
          : '—',
      },
    ]
  })

  const networkNotice = computed<{
    type: 'success' | 'warning' | 'info'
    title: string
    description: string
  } | null>(() => {
    const data = overview.value
    if (!data) return null
    const network = data.network
    if (!network) {
      return {
        type: 'info',
        title: '联网检索状态未上报',
        description: OFFLINE_SEARCH_TIP,
      }
    }
    if (network.mode === 'online') {
      return {
        type: 'success',
        title: `联网检索正常${network.provider ? `（${network.provider}）` : ''}`,
        description: '',
      }
    }
    if (network.mode === 'degraded') {
      return {
        type: 'warning',
        title: '联网检索受限，部分联网能力可能不可用',
        description: network.reason || OFFLINE_SEARCH_TIP,
      }
    }
    return {
      type: 'warning',
      title: '当前设备离线，联网插件或技能不可用',
      description: network.reason || OFFLINE_SEARCH_TIP,
    }
  })

  const agentRows = computed<AgentRow[]>(() =>
    agentItems.value.map(item => ({
      code: String(item.code ?? ''),
      name: String(item.name ?? item.code ?? '未命名 Agent'),
      calls: Number(item.calls ?? 0),
      failed: Number(item.failed ?? 0),
      total_tokens: Number(item.total_tokens ?? 0),
    })),
  )

  async function loadOverview(): Promise<void> {
    overviewState.value = 'loading'
    try {
      overview.value = await ownerApi.overview()
      overviewState.value = 'ready'
    } catch (e) {
      overviewState.value = classifyError(e)
    }
  }

  async function loadUsage(): Promise<void> {
    usageState.value = 'loading'
    try {
      const data = await ownerApi.memberUsage(7)
      memberUsageItems.value = data.items ?? []
      usageSummary.value = data.summary ?? {}
      sensitive.value = data.sensitive === true
      usageState.value = memberUsageItems.value.length ? 'ready' : 'empty'
    } catch (e) {
      usageState.value = classifyError(e)
    }
  }

  async function loadDaily(): Promise<void> {
    dailyState.value = 'loading'
    try {
      const data = await ownerApi.dailyUsage(7)
      dailyItems.value = data.items ?? []
      dailyState.value = dailyItems.value.length ? 'ready' : 'empty'
    } catch (e) {
      dailyState.value = classifyError(e)
    }
  }

  async function loadAgents(): Promise<void> {
    agentState.value = 'loading'
    try {
      const data = await ownerApi.agentUsage(7)
      agentItems.value = data.items ?? []
      agentState.value = agentItems.value.length ? 'ready' : 'empty'
    } catch (e) {
      agentState.value = classifyError(e)
    }
  }

  async function loadGrants(): Promise<void> {
    grantState.value = 'loading'
    try {
      const data = await ownerApi.grants()
      grantItems.value = data.items ?? []
      grantState.value = grantItems.value.length ? 'ready' : 'empty'
    } catch (e) {
      grantState.value = classifyError(e)
    }
  }

  async function saveGrants(userId: string, grants: string[]): Promise<void> {
    savingGrantId.value = userId
    try {
      await ownerApi.updateGrants(userId, grants)
      ElMessage.success('授权已更新')
      await loadGrants()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      savingGrantId.value = ''
    }
  }

  async function refreshAll(): Promise<void> {
    refreshing.value = true
    try {
      await Promise.allSettled([loadOverview(), loadUsage(), loadDaily(), loadAgents(), loadGrants()])
    } finally {
      refreshing.value = false
    }
  }

  async function onTransferred(): Promise<void> {
    try {
      await auth.fetchMe()
    } catch {
      /* 角色刷新失败时仍返回工作台，页面内会显示 403 空态 */
    }
    await router.replace('/')
  }

  onMounted(() => {
    if (!isOwner.value) return
    void refreshAll()
  })
</script>

<style scoped>
  .owner-view {
    padding: 4px;
  }
  .page-head {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .head-main .page-title {
    margin-bottom: 4px;
  }
  .page-sub {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 12px;
  }
  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }
  @media (max-width: 1024px) {
    .stat-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .stat-card {
    padding: 14px 16px;
  }
  .stat-label {
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .stat-value {
    margin-top: 6px;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ds-text-1);
    font-variant-numeric: tabular-nums;
  }
  .stat-hint {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
  }
  .notice-stack {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 14px;
  }
  .section {
    margin-top: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .summary-bar {
    margin-bottom: 10px;
    padding: 8px 12px;
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    font-size: 12px;
    color: var(--ds-text-2);
    font-variant-numeric: tabular-nums;
  }
  .member-cell {
    display: flex;
    flex-direction: column;
    line-height: 1.4;
  }
  .member-name {
    color: var(--ds-text-1);
    font-weight: 500;
  }
  .member-sub {
    font-size: 11px;
    color: var(--ds-text-3);
    font-family: ui-monospace, monospace;
  }
  .contact-text {
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .section-tip {
    margin: 0 0 12px;
    font-size: 12px;
    color: var(--ds-text-2);
    line-height: 1.6;
  }
  .danger-section {
    border-color: rgba(248, 113, 113, 0.35);
  }
</style>
