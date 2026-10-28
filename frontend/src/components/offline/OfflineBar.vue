<template>
  <div
    v-if="ops.length"
    class="offline-bar"
  >
    <el-alert
      :type="hasConflict ? 'warning' : 'info'"
      :closable="false"
      show-icon
      class="offline-alert"
    >
      <template #title>
        <span class="offline-title">
          离线待同步 {{ ops.length }} 条
          <template v-if="conflictOps.length">（其中 {{ conflictOps.length }} 条冲突）</template>
          <template v-if="failedOps.length">（其中 {{ failedOps.length }} 条失败）</template>
        </span>
      </template>
      <template #default>
        <div class="offline-row">
          <span class="offline-note">
            这些修改已保存在本机，联网后会自动补发；不会丢失。
          </span>
          <div class="offline-actions">
            <el-button
              size="small"
              :loading="flushing"
              @click="syncNow"
            >
              立即同步
            </el-button>
            <el-button
              size="small"
              text
              @click="dialogVisible = true"
            >
              查看明细
            </el-button>
          </div>
        </div>
      </template>
    </el-alert>

    <el-dialog
      v-model="dialogVisible"
      title="离线待同步明细"
      width="640px"
    >
      <el-table
        :data="ops"
        size="small"
      >
        <el-table-column
          prop="label"
          label="操作"
          min-width="120"
        />
        <el-table-column
          label="状态"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="stateType(row.state)"
            >
              {{ stateLabel(row.state) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="说明"
          min-width="220"
        >
          <template #default="{ row }">
            <span v-if="row.state === 'conflict'" class="conflict-text">
              服务器上的记录已被他人修改
              <template v-if="row.serverCurrent?.title">
                （当前：{{ row.serverCurrent.title }}）
              </template>
            </span>
            <span v-else-if="row.error">{{ row.error }}</span>
            <span v-else>等待联网补发</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="190"
        >
          <template #default="{ row }">
            <template v-if="row.state === 'conflict'">
              <el-button
                text
                size="small"
                type="primary"
                @click="keepMine(row as OfflineOp)"
              >
                用我的覆盖
              </el-button>
              <el-button
                text
                size="small"
                type="danger"
                @click="discard(row as OfflineOp)"
              >
                放弃
              </el-button>
            </template>
            <el-button
              v-else
              text
              size="small"
              type="danger"
              @click="discard(row as OfflineOp)"
            >
              放弃
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="dialogVisible = false">
          关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onUnmounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    flush,
    resolveConflictDiscard,
    resolveConflictKeepMine,
    subscribe,
    type OfflineOp,
    type OfflineOpState,
  } from '@/offline/queue'

  const ops = ref<OfflineOp[]>([])
  const dialogVisible = ref(false)
  const flushing = ref(false)

  const conflictOps = computed(() => ops.value.filter(op => op.state === 'conflict'))
  const failedOps = computed(() => ops.value.filter(op => op.state === 'failed'))
  const hasConflict = computed(() => conflictOps.value.length > 0)

  const unsubscribe = subscribe(list => {
    ops.value = list
  })
  onUnmounted(unsubscribe)

  function stateType(state: OfflineOpState) {
    if (state === 'conflict') return 'warning'
    if (state === 'failed') return 'danger'
    return 'info'
  }

  function stateLabel(state: OfflineOpState) {
    if (state === 'conflict') return '冲突'
    if (state === 'failed') return '失败'
    return '待同步'
  }

  async function syncNow() {
    flushing.value = true
    try {
      const result = await flush()
      if (result.synced) ElMessage.success(`已同步 ${result.synced} 条修改`)
      if (result.conflicts) ElMessage.warning(`有 ${result.conflicts} 条修改与服务器冲突，请选择保留哪个版本`)
      if (result.failed) ElMessage.error(`有 ${result.failed} 条修改被服务器拒绝，请查看明细`)
      if (result.offline) ElMessage.info('仍未联网，修改继续保存在本机')
    } finally {
      flushing.value = false
    }
  }

  async function keepMine(op: OfflineOp) {
    await resolveConflictKeepMine(op)
    const result = await flush()
    if (result.synced) ElMessage.success('已用你的版本覆盖并同步')
    else if (result.conflicts) ElMessage.warning('服务器上又出现了新版本，请再确认一次')
    else if (result.failed) ElMessage.error('服务器拒绝了这条修改，请查看明细')
  }

  async function discard(op: OfflineOp) {
    await resolveConflictDiscard(op)
    ElMessage.success('已放弃这条本地修改')
  }
</script>

<style scoped>
  .offline-bar {
    margin-bottom: 12px;
  }
  .offline-title {
    font-weight: 600;
  }
  .offline-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .offline-note {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .offline-actions {
    display: flex;
    gap: 8px;
  }
  .conflict-text {
    color: var(--el-color-warning);
  }
</style>
