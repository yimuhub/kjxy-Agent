<template>
  <div class="settings-grants">
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>敏感授权中心</span>
          <el-button
            size="small"
            @click="loadGrants"
          >
            刷新
          </el-button>
        </div>
      </template>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        class="grant-alert"
        title="管理员默认看不到敏感数据。授权后管理员查看会话正文仍需填写原因并留审计。"
      />
      <AsyncState
        :state="state"
        empty-text="暂无小院管理员，邀请或设置为管理员后可逐项授权"
        @retry="loadGrants"
      >
        <GrantMatrix
          :items="items"
          :saving-id="savingId"
          @save="saveGrants"
        />
      </AsyncState>
      <p class="foot-note">
        「敏感数据」包含全小院会话正文与成员联系方式；「财务数据」「审计日志」按需逐项开放。
        授权变更会写入审计日志。
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { ownerApi, type GrantRow } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import GrantMatrix from '@/components/settings/GrantMatrix.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const state = ref<ViewState>('loading')
  const items = ref<GrantRow[]>([])
  const savingId = ref('')

  async function loadGrants(): Promise<void> {
    state.value = 'loading'
    try {
      const data = await ownerApi.grants()
      items.value = data.items ?? []
      state.value = items.value.length ? 'ready' : 'empty'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function saveGrants(userId: string, grants: string[]): Promise<void> {
    savingId.value = userId
    try {
      await ownerApi.updateGrants(userId, grants)
      ElMessage.success('授权已更新')
      await loadGrants()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      savingId.value = ''
    }
  }

  onMounted(() => {
    void loadGrants()
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
  .grant-alert {
    margin-bottom: 14px;
  }
  .foot-note {
    margin: 12px 0 0;
    color: var(--ds-text-3);
    font-size: 12px;
    line-height: 1.6;
  }
</style>
