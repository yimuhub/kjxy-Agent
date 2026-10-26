<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>会话审计</span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="loadSessions"
          >
            刷新
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="audit-alert"
      title="查看会话正文必须填写原因；本次查看将记录审计（原因仅写入审计元数据）。"
    />

    <el-alert
      v-if="forbidden"
      type="error"
      :closable="false"
      show-icon
      class="audit-alert"
      title="当前账号没有敏感数据权限，请联系小院负责人授权后再查看会话正文。"
    />

    <div class="audit-filters">
      <el-select
        v-model="userId"
        placeholder="全部成员"
        clearable
        filterable
        style="width: 200px"
        @change="applyFilters"
      >
        <el-option
          v-for="member in memberOptions"
          :key="member.user_id"
          :label="member.display_name || member.username"
          :value="member.user_id"
        />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="搜索会话标题 / 成员"
        clearable
        style="width: 240px"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <el-button
        type="primary"
        size="small"
        @click="applyFilters"
      >
        查询
      </el-button>
      <span class="filter-total">共 {{ total }} 个会话</span>
    </div>

    <AsyncState
      :state="state"
      empty-text="暂无会话记录"
      @retry="loadSessions"
    >
      <el-table
        :data="rows"
        stripe
      >
        <el-table-column
          label="会话"
          min-width="220"
        >
          <template #default="{ row }">
            <div class="session-title">
              {{ row.title || '未命名会话' }}
            </div>
            <div class="session-id">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column
          label="发起人"
          width="160"
        >
          <template #default="{ row }">
            {{ row.created_by_display_name || row.created_by_username || row.created_by || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="消息数"
          width="90"
        >
          <template #default="{ row }">
            {{ row.message_count ?? 0 }}
          </template>
        </el-table-column>
        <el-table-column
          label="最近更新"
          width="170"
        >
          <template #default="{ row }">
            {{ formatTime(row.updated_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="Token"
          width="110"
        >
          <template #default="{ row }">
            {{ formatNumber(row.total_tokens) }}
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="130"
        >
          <template #default="{ row }">
            <el-tooltip
              :disabled="!forbidden"
              content="无敏感数据权限，请联系小院负责人授权"
              placement="top"
            >
              <span>
                <el-button
                  link
                  type="primary"
                  :disabled="forbidden"
                  @click="openReason(row as SessionRow)"
                >
                  查看正文
                </el-button>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="loadSessions"
        />
      </div>
    </AsyncState>

    <el-dialog
      v-model="reasonDialog"
      title="查看会话正文"
      width="520px"
      @closed="reason = ''"
    >
      <p class="dialog-tip">
        会话「{{ target?.title || '未命名会话' }}」将展示完整消息正文。请填写查看原因，
        <strong>本次查看将记录审计</strong>。
      </p>
      <el-input
        v-model="reason"
        type="textarea"
        :rows="3"
        maxlength="200"
        show-word-limit
        placeholder="请填写 2-200 字原因，如：排查用户反馈的回答异常"
      />
      <div class="reason-counter">
        {{ reason.trim().length }} / 200 字（至少 2 字）
      </div>
      <template #footer>
        <el-button @click="reasonDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="viewing"
          :disabled="!reasonValid"
          @click="confirmView"
        >
          确认查看（记录审计）
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="drawer"
      :title="`会话正文：${activeSession?.title || '未命名会话'}`"
      size="580px"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="drawer-alert"
        title="本次查看已记录审计"
        :description="`查看原因：${viewReason}`"
      />
      <div class="drawer-meta">
        <span>会话 ID：{{ activeSession?.id }}</span>
        <span>发起人：{{ activeSession?.created_by_display_name || activeSession?.created_by }}</span>
        <span>消息数：{{ messages.length }}</span>
      </div>
      <div
        v-if="messages.length"
        class="message-list"
      >
        <div
          v-for="message in messages"
          :key="message.id"
          class="message-item"
          :class="message.role === 'user' ? 'is-user' : 'is-assistant'"
        >
          <div class="message-head">
            <span class="message-role">{{ roleLabel(message.role) }}</span>
            <el-tag
              v-if="message.status === 'failed'"
              size="small"
              type="danger"
            >
              发送失败
            </el-tag>
            <span class="message-time">{{ formatTime(message.created_at) }}</span>
          </div>
          <div class="message-content">
            {{ message.content || '（空消息）' }}
          </div>
        </div>
      </div>
      <el-empty
        v-else
        description="该会话暂无消息"
      />
    </el-drawer>
  </el-card>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { ownerApi, type MemberUsageRow, type OwnerSessionRow } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { formatTime } from '@/utils/format'
  import { classifyError, type ViewState } from '@/utils/status'

  interface SessionRow extends OwnerSessionRow {
    created_by_username?: string | null
    created_by_display_name?: string | null
    total_tokens?: number
  }

  interface AuditMessage {
    id: string
    role: string
    content: string
    status: string
    created_at: string
  }

  const props = withDefaults(defineProps<{ members?: MemberUsageRow[] }>(), { members: () => [] })

  const rows = ref<SessionRow[]>([])
  const state = ref<ViewState>('loading')
  const page = ref(1)
  const pageSize = ref(10)
  const total = ref(0)
  const userId = ref('')
  const keyword = ref('')
  const forbidden = ref(false)

  const reasonDialog = ref(false)
  const reason = ref('')
  const viewing = ref(false)
  const target = ref<SessionRow | null>(null)
  const activeSession = ref<SessionRow | null>(null)
  const viewReason = ref('')
  const drawer = ref(false)
  const messages = ref<AuditMessage[]>([])

  const memberOptions = computed(() => props.members)

  const reasonValid = computed(() => {
    const length = reason.value.trim().length
    return length >= 2 && length <= 200
  })

  function formatNumber(value?: number | null): string {
    return new Intl.NumberFormat('zh-CN').format(Number(value ?? 0))
  }

  function roleLabel(role: string): string {
    if (role === 'user') return '用户'
    if (role === 'assistant') return '助手'
    return role || '消息'
  }

  function toMessage(raw: Record<string, unknown>, index: number): AuditMessage {
    return {
      id: String(raw.id ?? `msg_${index}`),
      role: String(raw.role ?? ''),
      content: typeof raw.content === 'string' ? raw.content : '',
      status: String(raw.status ?? ''),
      created_at: String(raw.created_at ?? ''),
    }
  }

  async function loadSessions(): Promise<void> {
    state.value = 'loading'
    try {
      const data = await ownerApi.sessions({
        page: page.value,
        page_size: pageSize.value,
        user_id: userId.value || undefined,
        keyword: keyword.value.trim() || undefined,
      })
      rows.value = data.items as unknown as SessionRow[]
      total.value = data.total
      forbidden.value = false
      state.value = rows.value.length ? 'ready' : 'empty'
    } catch (e) {
      if (classifyError(e) === 'forbidden') forbidden.value = true
      state.value = classifyError(e)
    }
  }

  function applyFilters(): void {
    page.value = 1
    void loadSessions()
  }

  function openReason(row: SessionRow): void {
    if (forbidden.value) return
    target.value = row
    reason.value = ''
    reasonDialog.value = true
  }

  async function confirmView(): Promise<void> {
    if (!target.value) return
    if (!reasonValid.value) {
      ElMessage.warning('请填写 2-200 字的查看原因')
      return
    }
    viewing.value = true
    try {
      const data = await ownerApi.sessionMessages(target.value.id, reason.value.trim())
      messages.value = (data.items ?? []).map((item, index) =>
        toMessage(item as Record<string, unknown>, index),
      )
      activeSession.value = target.value
      viewReason.value = reason.value.trim()
      reasonDialog.value = false
      drawer.value = true
      ElMessage.success('已记录审计，正文已打开')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      viewing.value = false
    }
  }

  onMounted(() => {
    void loadSessions()
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
  }
  .audit-alert {
    margin-bottom: 12px;
  }
  .audit-filters {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }
  .filter-total {
    margin-left: auto;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .session-title {
    color: var(--ds-text-1);
    font-weight: 500;
  }
  .session-id {
    font-family: ui-monospace, monospace;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .pager {
    display: flex;
    justify-content: flex-end;
    margin-top: 12px;
  }
  .dialog-tip {
    margin: 0 0 10px;
    color: var(--ds-text-2);
    font-size: 13px;
    line-height: 1.6;
  }
  .reason-counter {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
    text-align: right;
  }
  .drawer-alert {
    margin-bottom: 12px;
  }
  .drawer-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin-bottom: 14px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .message-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .message-item {
    padding: 10px 12px;
    border-radius: var(--ds-radius-control);
    border: 1px solid var(--ds-border);
    background: var(--ds-glass);
  }
  .message-item.is-user {
    border-color: rgba(99, 102, 241, 0.4);
  }
  .message-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .message-role {
    font-size: 12px;
    font-weight: 600;
    color: var(--ds-text-2);
  }
  .message-time {
    margin-left: auto;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .message-content {
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-1);
  }
</style>
