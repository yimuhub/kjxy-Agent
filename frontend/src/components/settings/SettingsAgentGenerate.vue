<template>
  <div class="settings-agent-generate">
    <AsyncState
      :state="state"
      empty-text="暂无生成配置"
      @retry="loadSettings"
    >
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>一句话生成智能体</span>
            <el-button
              size="small"
              type="primary"
              :loading="saving"
              @click="save"
            >
              保存
            </el-button>
          </div>
        </template>

        <el-form
          label-width="140px"
          class="settings-form"
        >
          <el-form-item label="启用一句话生成">
            <el-switch v-model="cfg.enabled" />
            <span class="note">关闭后成员无法用一句话生成智能体草稿</span>
          </el-form-item>
          <el-form-item label="允许联网取证">
            <el-switch v-model="cfg.use_web" />
            <span class="note">关闭后仅依据本地知识库设计（检索失败本就会自动降级）</span>
          </el-form-item>
          <el-form-item label="每人每日上限">
            <el-input-number
              v-model="cfg.daily_limit"
              :min="0"
              :max="200"
            />
            <span class="note">0-200 次/人/天（0 = 不限）；生成草稿不自动发布</span>
          </el-form-item>
        </el-form>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="生成的智能体一律先落为草稿（编码带 gen_ 前缀），发布仍走现有分级审核闸门。"
        />
      </el-card>
    </AsyncState>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  /** 一句话生成智能体配额（v3.5）：enabled / daily_limit / use_web */
  const state = ref<ViewState>('loading')
  const saving = ref(false)

  const cfg = reactive({
    enabled: true,
    daily_limit: 10,
    use_web: true,
  })

  async function loadSettings(): Promise<void> {
    state.value = 'loading'
    try {
      const data = await governanceApi.settings()
      Object.assign(cfg, data.agent_generate)
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await governanceApi.updateSetting('agent_generate', {
        enabled: cfg.enabled,
        use_web: cfg.use_web,
        daily_limit: cfg.daily_limit,
      })
      ElMessage.success('智能体生成配置已保存')
      await loadSettings()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  onMounted(() => {
    void loadSettings()
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
  .note {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
