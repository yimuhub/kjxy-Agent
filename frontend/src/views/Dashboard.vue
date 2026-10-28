<template>
  <div class="dash">
    <header class="dash-head">
      <div>
        <h2 class="dash-title">
          今日工作台
        </h2>
        <p class="dash-sub">
          {{ greeting }}，{{ displayName }}。{{ summaryLine }}
        </p>
      </div>
      <el-button
        :loading="loading"
        @click="loadAll"
      >
        刷新
      </el-button>
    </header>

    <!-- 快捷动作：把每天真实要做的事放在第一屏 -->
    <section class="quick-actions">
      <el-button
        type="primary"
        @click="go('/farm')"
      >
        记一笔农事
      </el-button>
      <el-button @click="go('/diagnosis')">
        拍照诊断
      </el-button>
      <el-button @click="go('/work-orders')">
        上报告警
      </el-button>
      <el-button @click="go('/chat')">
        开始对话
      </el-button>
      <el-button @click="go('/farm')">
        生成本周报表
      </el-button>
    </section>

    <!-- 今日摘要：数字必须可点进对应列表 -->
    <el-row
      :gutter="14"
      class="stat-row"
    >
      <el-col
        v-for="card in summaryCards"
        :key="card.key"
        :xs="12"
        :sm="12"
        :md="6"
      >
        <button
          type="button"
          class="stat-card"
          :aria-label="`${card.label} ${card.value}，点击查看`"
          @click="go(card.to)"
        >
          <span class="stat-label">{{ card.label }}</span>
          <span
            class="stat-value"
            :class="{ warn: card.warn }"
          >{{ card.value }}</span>
          <span class="stat-desc">{{ card.desc }}</span>
        </button>
      </el-col>
    </el-row>

    <el-row :gutter="14">
      <el-col
        :xs="24"
        :md="8"
      >
        <el-card class="panel">
          <template #header>
            田间运行
          </template>
          <div
            v-if="loading"
            class="panel-loading"
          >
            加载中…
          </div>
          <FallbackBody
            v-else
            :state="fieldState"
            empty-text="还没有地块。先建地块，再记录农事。"
            @retry="loadAll"
          >
            <dl class="kv">
              <div>
                <dt>地块</dt>
                <dd>{{ plots.length }} 块</dd>
              </div>
              <div>
                <dt>今日记录</dt>
                <dd>{{ todayRecords }} 条 · 涉及 {{ todayPlots }} 块</dd>
              </div>
              <div>
                <dt>今日未记录</dt>
                <dd>{{ plots.length ? notRecordedPlots + ' 块' : '—' }}</dd>
              </div>
              <div>
                <dt>工单闭环率</dt>
                <dd>{{ closureRate }}</dd>
              </div>
            </dl>
          </FallbackBody>
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :md="8"
      >
        <el-card class="panel">
          <template #header>
            知识资产
          </template>
          <div
            v-if="loading"
            class="panel-loading"
          >
            加载中…
          </div>
          <FallbackBody
            v-else
            :state="kbState"
            empty-text="知识库为空，上传第一份资料后再提问。"
            @retry="loadAll"
          >
            <dl class="kv">
              <div>
                <dt>文档总数</dt>
                <dd>{{ docTotal }}</dd>
              </div>
              <div>
                <dt>解析失败</dt>
                <dd :class="{ warn: failedDocs > 0 }">
                  {{ failedDocs }}
                </dd>
              </div>
              <div v-if="canReview">
                <dt>待审核候选</dt>
                <dd>{{ pendingReview }}</dd>
              </div>
            </dl>
            <el-button
              link
              type="primary"
              @click="go('/knowledge')"
            >
              管理知识库
            </el-button>
          </FallbackBody>
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :md="8"
      >
        <el-card class="panel">
          <template #header>
            用量与配额
          </template>
          <div
            v-if="loading"
            class="panel-loading"
          >
            加载中…
          </div>
          <FallbackBody
            v-else
            :state="quotaState"
            empty-text="暂无配额数据"
            @retry="loadAll"
          >
            <div
              v-for="q in quotaRows"
              :key="q.kind"
              class="quota-row"
            >
              <div class="quota-head">
                <span>{{ q.label }}</span>
                <span class="muted">{{ q.used }} / {{ q.limit }}</span>
              </div>
              <el-progress
                :percentage="q.percent"
                :status="q.percent >= 90 ? 'warning' : undefined"
                :stroke-width="6"
                :show-text="false"
              />
            </div>
            <p class="muted quota-note">
              统计周期 {{ quotaPeriod || '本月' }}，配额用尽前会提醒管理员。
            </p>
          </FallbackBody>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="panel timeline-panel">
      <template #header>
        <div class="panel-head">
          <span>最近动态</span>
          <span class="muted">工单 · 诊断 · 农事记录</span>
        </div>
      </template>
      <div
        v-if="loading"
        class="panel-loading"
      >
        加载中…
      </div>
      <el-empty
        v-else-if="!feed.length"
        description="今天还没有新动态。记一笔农事或发起一次对话，这里会出现记录。"
      >
        <el-button
          type="primary"
          @click="go('/farm')"
        >
          记一笔农事
        </el-button>
      </el-empty>
      <ul
        v-else
        class="feed"
      >
        <li
          v-for="item in feed"
          :key="item.key"
          class="feed-item"
        >
          <span
            class="feed-kind"
            :class="item.tone"
          >{{ item.kindLabel }}</span>
          <button
            type="button"
            class="feed-title"
            @click="go(item.to)"
          >
            {{ item.title }}
          </button>
          <span class="feed-time">{{ item.timeText }}</span>
        </li>
      </ul>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { computed, defineComponent, h, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import {
    diagnosisApi,
    farmApi,
    knowledgeApi,
    workOrderApi,
    yardApi,
    type FarmAlert,
    type FarmDiagnosisSummary,
    type FarmPlot,
    type FarmRecord,
    type WorkOrder,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'
  import { parseApiTime } from '@/utils/format'

  interface FallbackBodyProps {
    state: 'loading' | 'ok' | 'error'
    emptyText: string
  }

  const FallbackBody = defineComponent<FallbackBodyProps>({
    props: {
      state: { type: String, required: true },
      emptyText: { type: String, required: true },
    },
    emits: ['retry'],
    setup(props, { slots, emit }) {
      return () => {
        if (props.state === 'error') {
          return h('div', { class: 'panel-error' }, [
            h('span', '加载失败'),
            h(
              'button',
              { type: 'button', class: 'retry', onClick: () => emit('retry') },
              '重试',
            ),
          ])
        }
        if (!slots.default) {
          return h('p', { class: 'muted' }, props.emptyText)
        }
        return slots.default()
      }
    },
  })

  const router = useRouter()
  const auth = useAuthStore()

  const canReview = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert']),
  )
  const canManage = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin']),
  )

  const displayName = computed(
    () => auth.user?.display_name || auth.user?.username || '你好',
  )
  const greeting = computed(() => {
    const hour = new Date().getHours()
    if (hour < 6) return '夜里好'
    if (hour < 12) return '早上好'
    if (hour < 18) return '下午好'
    return '晚上好'
  })

  const loading = ref(true)
  const fieldState = ref<'loading' | 'ok' | 'error'>('loading')
  const kbState = ref<'loading' | 'ok' | 'error'>('loading')
  const quotaState = ref<'loading' | 'ok' | 'error'>('loading')

  const plots = ref<FarmPlot[]>([])
  const records = ref<FarmRecord[]>([])
  const workOrders = ref<WorkOrder[]>([])
  const alerts = ref<FarmAlert[]>([])
  const diagnoses = ref<FarmDiagnosisSummary[]>([])
  const pendingReview = ref(0)
  const docTotal = ref(0)
  const failedDocs = ref(0)
  const quota = ref<Record<string, { used?: number; limit?: number; remaining?: number }>>({})
  const quotaPeriod = ref('')

  const today = (): string => {
    const now = new Date()
    const month = `${now.getMonth() + 1}`.padStart(2, '0')
    const day = `${now.getDate()}`.padStart(2, '0')
    return `${now.getFullYear()}-${month}-${day}`
  }

  const activeOrders = computed(() =>
    workOrders.value.filter(o => ['open', 'assigned', 'in_progress'].includes(o.status)),
  )
  const overdueOrders = computed(() => {
    const day = today()
    return activeOrders.value.filter(o => o.due_on && o.due_on < day).length
  })
  const todayRecords = computed(() => records.value.length)
  const todayPlots = computed(() => new Set(records.value.map(r => r.plot_id)).size)
  const notRecordedPlots = computed(() =>
    Math.max(plots.value.length - todayPlots.value, 0),
  )
  const closureRate = computed(() => {
    const finished = workOrders.value.filter(o => ['done', 'closed'].includes(o.status)).length
    const counted = workOrders.value.filter(o => o.status !== 'cancelled').length
    if (!counted) return '—'
    return `${Math.round((finished / counted) * 100)}%`
  })

  const pendingDiagnoses = computed(() => diagnoses.value.length)

  const summaryLine = computed(() => {
    if (loading.value) return '正在汇总今天的待办…'
    const items: string[] = []
    if (activeOrders.value.length) {
      items.push(`${activeOrders.value.length} 张工单待处理${overdueOrders.value ? `（${overdueOrders.value} 张已逾期）` : ''}`)
    }
    if (pendingDiagnoses.value) items.push(`${pendingDiagnoses.value} 条诊断待跟进`)
    if (notRecordedPlots.value) items.push(`${notRecordedPlots.value} 块地今天还没记录`)
    if (!items.length) return '今天没有紧急待办，可以从记一笔农事开始。'
    return items.join('，') + '。'
  })

  const summaryCards = computed(() => [
    {
      key: 'orders',
      label: canManage.value ? '待处理工单' : '我的待办工单',
      value: activeOrders.value.length,
      desc: overdueOrders.value ? `${overdueOrders.value} 张已逾期` : '全部在期限内',
      warn: overdueOrders.value > 0,
      to: '/work-orders',
    },
    {
      key: 'diagnoses',
      label: canReview.value ? '待专家复核' : '进行中诊断',
      value: pendingDiagnoses.value,
      desc: canReview.value ? '复核后用药建议才可执行' : '可在作物诊断中跟进',
      warn: pendingDiagnoses.value > 0,
      to: '/diagnosis',
    },
    {
      key: 'records',
      label: '今日农事记录',
      value: todayRecords.value,
      desc: plots.value.length
        ? `${notRecordedPlots.value} 块地未记录`
        : '先建立地块档案',
      warn: false,
      to: '/farm',
    },
    {
      key: 'alerts',
      label: '未处理告警',
      value: alerts.value.length,
      desc: alerts.value.length ? '严重告警会自动转工单' : '暂无新告警',
      warn: alerts.value.some(a => a.severity === 'critical'),
      to: '/work-orders',
    },
  ])

  const quotaLabels: Record<string, string> = {
    llm_calls: '模型调用',
    knowledge_docs: '知识文档',
    tasks: '后台任务',
  }
  const quotaRows = computed(() =>
    Object.entries(quota.value).map(([kind, item]) => {
      const used = item.used ?? 0
      const limit = item.limit ?? 0
      return {
        kind,
        label: quotaLabels[kind] || kind,
        used,
        limit,
        percent: limit > 0 ? Math.min(Math.round((used / limit) * 100), 100) : 0,
      }
    }),
  )

  interface FeedItem {
    key: string
    kindLabel: string
    tone: string
    title: string
    to: string
    ts: number
    timeText: string
  }

  function timeText(value?: string | null): string {
    const ts = parseApiTime(value)?.getTime()
    if (ts === undefined) return ''
    const diff = Date.now() - ts
    if (diff < 60_000) return '刚刚'
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
    return new Date(ts).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
  }

  const feed = computed<FeedItem[]>(() => {
    const items: FeedItem[] = []
    for (const o of workOrders.value.slice(0, 6)) {
      items.push({
        key: `wo-${o.work_order_id}`,
        kindLabel: '工单',
        tone: 'tone-order',
        title: o.title,
        to: '/work-orders',
        ts: parseApiTime(o.updated_at)?.getTime() ?? 0,
        timeText: timeText(o.updated_at),
      })
    }
    for (const d of diagnoses.value.slice(0, 5)) {
      items.push({
        key: `dx-${d.diagnosis_id}`,
        kindLabel: '诊断',
        tone: 'tone-diagnosis',
        title: `${d.crop} · ${d.status === 'expert_review' ? '待专家复核' : '待跟进'}`,
        to: '/diagnosis',
        ts: parseApiTime(d.created_at)?.getTime() ?? 0,
        timeText: timeText(d.created_at),
      })
    }
    for (const r of records.value.slice(0, 6)) {
      items.push({
        key: `rec-${r.record_id}`,
        kindLabel: '农事',
        tone: 'tone-record',
        title: `${r.plot_name || '地块'} · ${r.kind_label}${r.title ? `：${r.title}` : ''}`,
        to: '/farm',
        ts: parseApiTime(r.created_at)?.getTime() ?? 0,
        timeText: timeText(r.created_at),
      })
    }
    return items.sort((a, b) => b.ts - a.ts).slice(0, 8)
  })

  function go(path: string) {
    router.push(path)
  }

  async function loadAll() {
    loading.value = true
    fieldState.value = 'loading'
    kbState.value = 'loading'
    quotaState.value = 'loading'
    const day = today()
    const assignee = canManage.value ? undefined : auth.user?.id
    const results = await Promise.allSettled([
      farmApi.plots(),
      farmApi.records({ start: day, end: day, page_size: 100 }),
      workOrderApi.list({ page_size: 200, assignee_id: assignee }),
      workOrderApi.alerts({ status: 'new', page_size: 100 }),
      diagnosisApi.list({
        status: canReview.value ? 'expert_review' : 'open',
        page_size: 20,
      }),
      canReview.value
        ? knowledgeApi.candidates({ status: 'pending_review', page_size: 1 })
        : Promise.resolve({ items: [], meta: { total: 0 } }),
      knowledgeApi.list({ page: 1, page_size: 100 }),
      yardApi.getQuota(),
    ])
    const [
      plotsRes,
      recordsRes,
      ordersRes,
      alertsRes,
      diagRes,
      reviewRes,
      docsRes,
      quotaRes,
    ] = results

    if (plotsRes.status === 'fulfilled') {
      plots.value = plotsRes.value
      fieldState.value = 'ok'
    } else {
      plots.value = []
      fieldState.value = 'error'
    }
    records.value = recordsRes.status === 'fulfilled' ? recordsRes.value.items : []
    workOrders.value = ordersRes.status === 'fulfilled' ? ordersRes.value.items : []
    alerts.value = alertsRes.status === 'fulfilled' ? alertsRes.value.items : []
    diagnoses.value = diagRes.status === 'fulfilled' ? diagRes.value.items : []
    pendingReview.value =
      reviewRes.status === 'fulfilled' ? reviewRes.value.meta.total : 0
    if (docsRes.status === 'fulfilled') {
      docTotal.value = docsRes.value.meta.total
      failedDocs.value = docsRes.value.items.filter(d => d.status === 'failed').length
      kbState.value = 'ok'
    } else {
      docTotal.value = 0
      failedDocs.value = 0
      kbState.value = 'error'
    }
    if (quotaRes.status === 'fulfilled') {
      const data = quotaRes.value as {
        period?: string
        quotas?: Record<string, { used?: number; limit?: number; remaining?: number }>
      }
      quota.value = data.quotas ?? {}
      quotaPeriod.value = data.period ?? ''
      quotaState.value = 'ok'
    } else {
      quota.value = {}
      quotaState.value = 'error'
    }
    loading.value = false
  }

  onMounted(loadAll)
</script>

<style scoped>
  .dash {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .dash-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
  }
  .dash-title {
    margin: 0 0 4px;
    font-size: 22px;
    font-weight: 650;
  }
  .dash-sub {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 13px;
    line-height: 1.6;
  }
  .quick-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .stat-row {
    row-gap: 12px;
  }
  .stat-card {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    padding: 14px 16px;
    text-align: left;
    border-radius: 12px;
    border: 1px solid var(--ds-border);
    background: var(--ds-glass);
    color: var(--ds-text-1);
    cursor: pointer;
    transition:
      border-color 0.2s,
      transform 0.2s;
  }
  .stat-card:hover {
    border-color: var(--ds-border-strong);
    transform: translateY(-1px);
  }
  .stat-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .stat-value {
    font-size: 26px;
    font-weight: 650;
    line-height: 1.2;
  }
  .stat-value.warn {
    color: var(--el-color-warning);
  }
  .stat-desc {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .panel {
    height: 100%;
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .panel-loading {
    color: var(--ds-text-3);
    font-size: 13px;
    padding: 8px 0;
  }
  .panel-error {
    display: flex;
    gap: 10px;
    align-items: center;
    color: var(--ds-text-3);
    font-size: 13px;
  }
  .retry {
    border: none;
    background: transparent;
    color: var(--el-color-primary);
    cursor: pointer;
  }
  .kv {
    display: grid;
    gap: 10px;
    margin: 0 0 10px;
  }
  .kv div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
  }
  .kv dt {
    color: var(--ds-text-3);
  }
  .kv dd {
    margin: 0;
    font-weight: 600;
  }
  .warn {
    color: var(--el-color-warning);
  }
  .muted {
    color: var(--ds-text-3);
    font-size: 12px;
  }
  .quota-row {
    margin-bottom: 10px;
  }
  .quota-head {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-bottom: 4px;
  }
  .quota-note {
    margin: 6px 0 0;
  }
  .timeline-panel {
    margin-bottom: 8px;
  }
  .feed {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .feed-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 4px;
    border-bottom: 1px dashed var(--ds-border);
    font-size: 13px;
  }
  .feed-item:last-child {
    border-bottom: none;
  }
  .feed-kind {
    flex: none;
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 11px;
    border: 1px solid var(--ds-border);
    color: var(--ds-text-2);
  }
  .feed-kind.tone-order {
    color: var(--el-color-warning);
  }
  .feed-kind.tone-diagnosis {
    color: var(--el-color-primary);
  }
  .feed-kind.tone-record {
    color: var(--el-color-success);
  }
  .feed-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
    border: none;
    background: transparent;
    color: var(--ds-text-1);
    cursor: pointer;
    font-size: 13px;
  }
  .feed-title:hover {
    color: var(--el-color-primary);
  }
  .feed-time {
    flex: none;
    color: var(--ds-text-3);
    font-size: 12px;
  }
  @media (max-width: 768px) {
    .stat-value {
      font-size: 22px;
    }
  }
</style>
