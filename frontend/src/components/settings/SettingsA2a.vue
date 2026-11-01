<template>
  <div class="settings-a2a">
    <el-alert
      class="section"
      type="info"
      :closable="false"
      show-icon
      title="跨小院协作（A2A）"
      description="本平台以 A2A 协议对外开放只读技能（小院概况、知识检索），也可登记其他小院的平台并派发任务。入站令牌与小院绑定；协作方令牌加密保存、永不回显。"
    />

    <el-alert
      v-if="!canManage"
      class="section"
      type="warning"
      :closable="false"
      show-icon
      title="仅负责人或获得敏感授权的管理员可修改协作配置。"
    />

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>对外协作入口</span>
          <div class="page-actions">
            <el-button
              size="small"
              @click="load"
            >
              刷新
            </el-button>
            <el-button
              v-if="canManage"
              size="small"
              type="primary"
              @click="rotateToken"
            >
              {{ status?.endpoint ? '轮换令牌' : '生成令牌' }}
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="status && !status.enabled"
        type="warning"
        :closable="false"
        show-icon
        class="section"
        title="平台未开启 A2A（环境变量 A2A_ENABLED=false），Agent Card 与入站 RPC 均不可用。"
      />

      <el-descriptions
        :column="1"
        border
        size="small"
      >
        <el-descriptions-item label="Agent Card">
          <span class="mono">{{ status?.card_url || '—' }}</span>
          <el-button
            v-if="status?.card_url"
            link
            type="primary"
            size="small"
            @click="copyCard"
          >
            复制
          </el-button>
        </el-descriptions-item>
        <el-descriptions-item label="入站令牌">
          <template v-if="status?.endpoint">
            <span class="mono">{{ status.endpoint.token_prefix }}…</span>
            <el-tag
              size="small"
              :type="status.endpoint.enabled ? 'success' : 'info'"
            >
              {{ status.endpoint.enabled ? '已启用' : '已停用' }}
            </el-tag>
            <span class="muted">最近调用：{{ formatTime(status.endpoint.last_used_at) }}</span>
            <el-switch
              v-if="canManage"
              v-model="inboundEnabled"
              class="inbound-switch"
              size="small"
              active-text="启用入站"
              @change="toggleInbound"
            />
          </template>
          <span
            v-else
            class="muted"
          >尚未生成；生成后把令牌发给协作方（完整令牌只显示一次）</span>
        </el-descriptions-item>
        <el-descriptions-item label="开放技能">
          <el-tag
            v-for="skill in status?.skills || []"
            :key="skill.id"
            size="small"
            effect="plain"
            class="skill-tag"
            :title="skill.description"
          >
            {{ skill.name }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>协作方（{{ peers.length }}）</span>
          <el-button
            v-if="canManage"
            size="small"
            type="primary"
            @click="openCreate"
          >
            新增协作方
          </el-button>
        </div>
      </template>

      <el-table
        v-col-resize
        :data="peers"
        size="small"
        row-key="id"
        empty-text="尚未登记协作方"
      >
        <el-table-column
          prop="name"
          label="名称"
          min-width="140"
        />
        <el-table-column
          label="地址 / 名片"
          min-width="240"
        >
          <template #default="{ row }">
            <div class="mono">
              {{ row.base_url }}
            </div>
            <div
              v-if="row.card"
              class="muted"
            >
              {{ row.card.name }} · {{ row.card.skill_count }} 项技能
            </div>
            <div
              v-else
              class="muted"
            >
              尚未探测
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="令牌"
          width="90"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.token_configured ? 'success' : 'info'"
            >
              {{ row.token_configured ? '已配置' : '未配置' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          min-width="160"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.enabled ? 'success' : 'info'"
            >
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
            <span
              v-if="row.last_error"
              class="error-text"
              :title="row.last_error"
            >{{ row.last_error }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="260"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              link
              size="small"
              :loading="probingId === row.id"
              @click="probe(row as A2aPeer)"
            >
              探测
            </el-button>
            <el-button
              v-if="canManage"
              link
              size="small"
              @click="openTaskDialog(row as A2aPeer)"
            >
              发起任务
            </el-button>
            <el-button
              v-if="canManage"
              link
              size="small"
              @click="openEdit(row as A2aPeer)"
            >
              编辑
            </el-button>
            <el-button
              v-if="canManage"
              link
              size="small"
              type="danger"
              @click="removePeer(row as A2aPeer)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>任务记录（{{ tasks.length }}）</span>
          <div class="page-actions">
            <el-select
              v-model="directionFilter"
              size="small"
              style="width: 130px"
              @change="loadTasks"
            >
              <el-option
                label="全部方向"
                value=""
              />
              <el-option
                label="仅入站"
                value="inbound"
              />
              <el-option
                label="仅出站"
                value="outbound"
              />
            </el-select>
            <el-button
              size="small"
              @click="loadTasks"
            >
              刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        v-col-resize
        :data="tasks"
        size="small"
        row-key="id"
        empty-text="还没有协作任务"
        @row-click="openDetail"
      >
        <el-table-column
          label="时间"
          width="160"
        >
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="方向"
          width="80"
        >
          <template #default="{ row }">
            {{ row.direction === 'inbound' ? '入站' : '出站' }}
          </template>
        </el-table-column>
        <el-table-column
          prop="skill_id"
          label="技能"
          width="150"
        />
        <el-table-column
          label="状态"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="statusTagType(row.status)"
            >
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="结果 / 错误"
          min-width="260"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ row.error_message || row.result_text || '—' }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="tokenDialog"
      append-to-body
      title="入站令牌（只显示一次）"
      width="560px"
    >
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="关闭后无法再次查看；遗失请重新轮换。"
      />
      <div class="token-box mono">
        {{ issuedToken }}
      </div>
      <template #footer>
        <el-button
          type="primary"
          @click="copyToken"
        >
          复制令牌
        </el-button>
        <el-button @click="tokenDialog = false">
          我已保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="dialogVisible"
      append-to-body
      :title="editingId ? '编辑协作方' : '新增协作方'"
      width="560px"
    >
      <el-form
        label-width="110px"
      >
        <el-form-item label="名称">
          <el-input
            v-model="form.name"
            maxlength="120"
            placeholder="如：邻村科技小院"
          />
        </el-form-item>
        <el-form-item label="根地址">
          <el-input
            v-model="form.base_url"
            placeholder="如：https://yard.example.com"
          />
          <div class="muted form-hint">
            探测会依次请求 /.well-known/agent-card.json 与 /.well-known/agent.json
          </div>
        </el-form-item>
        <el-form-item label="访问令牌">
          <el-input
            v-model="form.token"
            type="password"
            show-password
            :placeholder="editingId && peerHasToken ? '留空表示沿用已存令牌' : '对接方要求的 Bearer 令牌（可选）'"
          />
          <el-checkbox
            v-if="editingId && peerHasToken"
            v-model="clearToken"
          >
            清空已存令牌
          </el-checkbox>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="savePeer"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="taskDialog"
      append-to-body
      title="发起协作任务"
      width="560px"
    >
      <el-form label-width="90px">
        <el-form-item label="协作方">
          <span>{{ taskPeer?.name }}</span>
        </el-form-item>
        <el-form-item label="技能">
          <el-select
            v-model="taskForm.skill_id"
            style="width: 100%"
          >
            <el-option
              v-for="skill in status?.skills || []"
              :key="skill.id"
              :label="`${skill.name}（${skill.id}）`"
              :value="skill.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="输入">
          <el-input
            v-model="taskForm.input_text"
            type="textarea"
            :rows="4"
            maxlength="8000"
            placeholder="给对方的任务描述（知识检索技能需填检索文本）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="taskDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="sending"
          @click="sendTask"
        >
          发送
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="detailVisible"
      append-to-body
      title="任务详情"
      width="640px"
    >
      <el-descriptions
        v-if="detailTask"
        :column="1"
        border
        size="small"
      >
        <el-descriptions-item label="任务号">
          <span class="mono">{{ detailTask.id }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="方向 / 技能">
          {{ detailTask.direction === 'inbound' ? '入站' : '出站' }} · {{ detailTask.skill_id }}
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag
            size="small"
            :type="statusTagType(detailTask.status)"
          >
            {{ statusLabel(detailTask.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          v-if="detailTask.error_message"
          label="错误"
        >
          {{ detailTask.error_message }}（{{ detailTask.error_code }}）
        </el-descriptions-item>
        <el-descriptions-item label="结果">
          <pre class="result-pre">{{ detailTask.result_text || '—' }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import {
    a2aApi,
    type A2aPeer,
    type A2aPeerPayload,
    type A2aStatus,
    type A2aTask,
    type A2aTaskStatus,
  } from '@/api/v3'
  import { formatTime } from '@/utils/format'

  defineProps<{ canManage: boolean }>()

  const status = ref<A2aStatus | null>(null)
  const peers = ref<A2aPeer[]>([])
  const tasks = ref<A2aTask[]>([])
  const inboundEnabled = ref(false)
  const directionFilter = ref('')
  const probingId = ref('')

  const tokenDialog = ref(false)
  const issuedToken = ref('')

  const dialogVisible = ref(false)
  const editingId = ref<string | null>(null)
  const peerHasToken = ref(false)
  const clearToken = ref(false)
  const saving = ref(false)
  const form = reactive({ name: '', base_url: '', token: '', enabled: true })

  const taskDialog = ref(false)
  const taskPeer = ref<A2aPeer | null>(null)
  const taskForm = reactive({ skill_id: 'yard.summary', input_text: '' })
  const sending = ref(false)

  const detailVisible = ref(false)
  const detailTask = ref<A2aTask | null>(null)

  const statusLabels: Record<A2aTaskStatus, string> = {
    submitted: '已提交',
    working: '进行中',
    completed: '已完成',
    failed: '失败',
    canceled: '已取消',
  }

  function statusLabel(value: A2aTaskStatus): string {
    return statusLabels[value] ?? value
  }

  function statusTagType(value: A2aTaskStatus) {
    if (value === 'completed') return 'success'
    if (value === 'failed') return 'danger'
    if (value === 'canceled') return 'info'
    return 'warning'
  }

  async function load() {
    try {
      const [s, p] = await Promise.all([a2aApi.status(), a2aApi.listPeers()])
      status.value = s
      peers.value = p
      inboundEnabled.value = Boolean(s.endpoint?.enabled)
      await loadTasks()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '加载协作配置失败')
    }
  }

  async function loadTasks() {
    try {
      tasks.value = await a2aApi.tasks({
        direction: directionFilter.value || undefined,
        limit: 50,
      })
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '加载任务记录失败')
    }
  }

  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text)
      ElMessage.success(`${label}已复制`)
    } catch {
      ElMessage.warning('复制失败，请手动选择文本')
    }
  }

  function copyCard() {
    if (status.value?.card_url) copyText(status.value.card_url, 'Agent Card 地址')
  }

  function copyToken() {
    if (issuedToken.value) copyText(issuedToken.value, '令牌')
  }

  async function rotateToken() {
    const action = status.value?.endpoint ? '轮换' : '生成'
    try {
      await ElMessageBox.confirm(
        status.value?.endpoint
          ? '轮换后旧令牌立即失效，正在使用旧令牌的协作方需要更新。'
          : '生成后完整令牌只显示一次，请立即保存。',
        `${action}入站令牌`,
        { type: 'warning', confirmButtonText: action },
      )
    } catch {
      return
    }
    try {
      const res = await a2aApi.rotateToken()
      issuedToken.value = res.token
      tokenDialog.value = true
      await load()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '操作失败')
    }
  }

  async function toggleInbound(raw: string | number | boolean) {
    const value = Boolean(raw)
    try {
      await a2aApi.setInbound(value)
      ElMessage.success(value ? '已启用入站协作' : '已停用入站协作')
    } catch (err) {
      inboundEnabled.value = !value
      ElMessage.error(extractErrorMessage(err) || '操作失败')
    }
  }

  function emptyForm() {
    return { name: '', base_url: '', token: '', enabled: true }
  }

  function openCreate() {
    editingId.value = null
    peerHasToken.value = false
    clearToken.value = false
    Object.assign(form, emptyForm())
    dialogVisible.value = true
  }

  function openEdit(row: A2aPeer) {
    editingId.value = row.id
    peerHasToken.value = row.token_configured
    clearToken.value = false
    Object.assign(form, {
      name: row.name,
      base_url: row.base_url,
      token: '',
      enabled: row.enabled,
    })
    dialogVisible.value = true
  }

  async function savePeer() {
    if (!form.name.trim() || !form.base_url.trim()) {
      ElMessage.warning('名称与根地址不能为空')
      return
    }
    saving.value = true
    try {
      if (editingId.value) {
        const payload: Partial<A2aPeerPayload> = {
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          enabled: form.enabled,
        }
        if (clearToken.value) payload.clear_token = true
        else if (form.token) payload.token = form.token
        await a2aApi.updatePeer(editingId.value, payload)
        ElMessage.success('已保存')
      } else {
        await a2aApi.createPeer({
          name: form.name.trim(),
          base_url: form.base_url.trim(),
          token: form.token || undefined,
          enabled: form.enabled,
        })
        ElMessage.success('已添加；建议随即点击「探测」拉取 Agent Card')
      }
      dialogVisible.value = false
      await load()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '保存失败')
    } finally {
      saving.value = false
    }
  }

  async function removePeer(row: A2aPeer) {
    try {
      await ElMessageBox.confirm(`删除协作方「${row.name}」？历史任务记录会保留。`, '删除协作方', {
        type: 'warning',
      })
    } catch {
      return
    }
    try {
      await a2aApi.removePeer(row.id)
      ElMessage.success('已删除')
      await load()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '删除失败')
    }
  }

  async function probe(row: A2aPeer) {
    probingId.value = row.id
    try {
      const res = await a2aApi.probe(row.id)
      ElMessage.success(
        res.card?.name ? `已连接：${res.card.name}（${res.card.skill_count} 项技能）` : '探测成功',
      )
      await load()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '探测失败')
      await load()
    } finally {
      probingId.value = ''
    }
  }

  function openTaskDialog(row: A2aPeer) {
    taskPeer.value = row
    taskForm.skill_id = status.value?.skills[0]?.id || 'yard.summary'
    taskForm.input_text = ''
    taskDialog.value = true
  }

  async function sendTask() {
    if (!taskPeer.value) return
    sending.value = true
    try {
      const task = await a2aApi.sendTask(taskPeer.value.id, {
        skill_id: taskForm.skill_id,
        input_text: taskForm.input_text,
      })
      if (task.status === 'completed') {
        ElMessage.success('协作方已完成任务，可在下方记录里查看结果')
      } else {
        ElMessage.warning(
          task.error_message || `任务状态：${statusLabel(task.status)}`,
        )
      }
      taskDialog.value = false
      await loadTasks()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err) || '发送失败')
    } finally {
      sending.value = false
    }
  }

  function openDetail(row: A2aTask) {
    detailTask.value = row
    detailVisible.value = true
  }

  onMounted(load)
</script>

<style scoped>
.settings-a2a .section {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
.muted {
  margin-left: 8px;
  font-size: 12px;
  color: var(--ds-text-3);
}
.inbound-switch {
  margin-left: 12px;
}
.skill-tag {
  margin-right: 6px;
}
.error-text {
  margin-left: 8px;
  font-size: 12px;
  color: var(--ds-danger, #c45656);
}
.form-hint {
  margin-top: 4px;
  margin-left: 0;
}
.token-box {
  margin-top: 12px;
  padding: 12px;
  border: 1px dashed var(--ds-border, #dcdfe6);
  border-radius: 8px;
  background: var(--ds-bg-2, #f7f8fa);
}
.result-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
</style>
