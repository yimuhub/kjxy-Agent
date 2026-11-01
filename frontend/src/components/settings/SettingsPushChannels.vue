<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>消息推送</span>
        <el-button
          v-if="canManage"
          size="small"
          type="primary"
          @click="openCreate"
        >
          新增通道
        </el-button>
      </div>
    </template>

    <el-alert
      v-if="!canManage"
      type="info"
      :closable="false"
      show-icon
      title="仅小院管理员可配置推送通道。"
      style="margin-bottom: 12px"
    />
    <p class="note block">
      企业微信/钉钉群机器人或通用 Webhook：Agent 自动执行出的预警、工单与运行摘要会按订阅事件推送。
      URL 含密钥，保存后只显示掩码；连续失败 5 次会自动停用，重填 URL 即可恢复。
    </p>

    <el-empty
      v-if="!loading && !channels.length"
      description="还没有推送通道。配置后 Agent 的预警才能主动发到群里。"
      :image-size="70"
    />
    <el-table
      v-col-resize
      v-else
      v-loading="loading"
      :data="channels"
      size="small"
    >
      <el-table-column
        prop="name"
        label="名称"
        width="160"
        show-overflow-tooltip
      />
      <el-table-column
        label="类型"
        width="120"
      >
        <template #default="{ row }">
          {{ KIND_LABEL[row.kind] || row.kind }}
        </template>
      </el-table-column>
      <el-table-column
        prop="url_masked"
        label="地址"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column
        label="订阅事件"
        min-width="220"
      >
        <template #default="{ row }">
          <el-tag
            v-for="event in row.events"
            :key="event"
            size="small"
            class="event-tag"
          >
            {{ EVENT_LABEL[event] || event }}
          </el-tag>
          <span v-if="!row.events.length">—</span>
        </template>
      </el-table-column>
      <el-table-column
        label="状态"
        width="120"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.enabled ? 'success' : 'info'"
          >
            {{ row.enabled ? '启用' : '停用' }}
          </el-tag>
          <span
            v-if="row.consecutive_failures"
            class="fail-count"
          >连续失败 {{ row.consecutive_failures }}</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="canManage"
        label="操作"
        width="150"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            @click="openEdit(row as PushChannel)"
          >
            编辑
          </el-button>
          <el-button
            link
            type="danger"
            @click="remove(row as PushChannel)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dialog"
      append-to-body
      :title="editing ? '编辑推送通道' : '新增推送通道'"
      width="620px"
    >
      <el-form
        label-width="110px"
        class="settings-form"
      >
        <el-form-item label="名称">
          <el-input
            v-model="form.name"
            maxlength="120"
            placeholder="例如：小院管理群"
          />
        </el-form-item>
        <el-form-item label="类型">
          <el-select
            v-model="form.kind"
            style="width: 220px"
          >
            <el-option
              label="企业微信群机器人"
              value="wecom_robot"
            />
            <el-option
              label="钉钉群机器人"
              value="dingtalk"
            />
            <el-option
              label="通用 Webhook"
              value="generic_webhook"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="Webhook URL">
          <el-input
            v-model="form.url"
            type="password"
            show-password
            :placeholder="editing?.url_masked ? `已保存（${editing.url_masked}），留空表示不修改` : 'https://...'"
          />
        </el-form-item>
        <el-form-item label="订阅事件">
          <el-select
            v-model="form.events"
            multiple
            collapse-tags
            collapse-tags-tooltip
            style="width: 100%"
          >
            <el-option
              v-for="opt in PUSH_EVENT_OPTIONS"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="submit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    PUSH_EVENT_OPTIONS,
    pushChannelApi,
    type PushChannel,
    type PushChannelKind,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'

  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())
  const loading = ref(false)
  const saving = ref(false)
  const channels = ref<PushChannel[]>([])
  const dialog = ref(false)
  const editing = ref<PushChannel | null>(null)

  const KIND_LABEL: Record<string, string> = {
    wecom_robot: '企业微信',
    dingtalk: '钉钉',
    generic_webhook: '通用 Webhook',
  }
  const EVENT_LABEL = Object.fromEntries(PUSH_EVENT_OPTIONS.map(opt => [opt.value, opt.label]))

  const form = reactive({
    name: '',
    kind: 'wecom_robot' as PushChannelKind,
    url: '',
    events: ['farm_alert', 'farm_agent_run'] as string[],
    enabled: true,
  })

  async function load(): Promise<void> {
    loading.value = true
    try {
      channels.value = await pushChannelApi.list()
    } catch {
      /* 统一 toast */
    } finally {
      loading.value = false
    }
  }

  function openCreate(): void {
    editing.value = null
    form.name = ''
    form.kind = 'wecom_robot'
    form.url = ''
    form.events = ['farm_alert', 'farm_agent_run']
    form.enabled = true
    dialog.value = true
  }

  function openEdit(row: PushChannel): void {
    editing.value = row
    form.name = row.name
    form.kind = row.kind
    form.url = ''
    form.events = [...row.events]
    form.enabled = row.enabled
    dialog.value = true
  }

  async function submit(): Promise<void> {
    if (!form.name.trim()) {
      ElMessage.warning('请填写通道名称')
      return
    }
    if (!form.events.length) {
      ElMessage.warning('至少订阅一个事件')
      return
    }
    if (!editing.value && !form.url.trim()) {
      ElMessage.warning('请填写 Webhook URL')
      return
    }
    saving.value = true
    try {
      if (editing.value) {
        const payload: Record<string, unknown> = {
          name: form.name.trim(),
          kind: form.kind,
          events: form.events,
          enabled: form.enabled,
        }
        if (form.url.trim()) payload.url = form.url.trim()
        await pushChannelApi.update(editing.value.id, payload)
        ElMessage.success('推送通道已更新')
      } else {
        await pushChannelApi.create({
          name: form.name.trim(),
          kind: form.kind,
          url: form.url.trim(),
          events: form.events,
          enabled: form.enabled,
        })
        ElMessage.success('推送通道已创建')
      }
      dialog.value = false
      await load()
    } catch {
      /* 统一 toast */
    } finally {
      saving.value = false
    }
  }

  async function remove(row: PushChannel): Promise<void> {
    try {
      await ElMessageBox.confirm(`删除推送通道「${row.name}」？删除后不再向该地址推送。`, '删除通道', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await pushChannelApi.remove(row.id)
      ElMessage.success('已删除')
      await load()
    } catch {
      /* 统一 toast */
    }
  }

  onMounted(load)
</script>

<style scoped>
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .event-tag {
    margin: 0 4px 4px 0;
  }
  .fail-count {
    display: block;
    font-size: 12px;
    color: var(--el-color-warning);
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .block {
    display: block;
    margin: 0 0 8px;
    line-height: 1.7;
  }
</style>
