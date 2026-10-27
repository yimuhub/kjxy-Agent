<template>
  <div class="settings-skill-lab">
    <AsyncState
      :state="state"
      empty-text="暂无技能生成配置"
      @retry="loadSettings"
    >
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>一句话技能生成</span>
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
            <el-switch v-model="lab.enabled" />
            <span class="note">关闭后所有一句话生成请求会被拒绝</span>
          </el-form-item>
          <el-form-item label="允许联网找资料">
            <el-switch v-model="lab.use_web" />
            <span class="note">关闭后仅依据本地知识库与内置技能生成</span>
          </el-form-item>
          <el-form-item label="每日生成上限">
            <el-input-number
              v-model="lab.daily_limit"
              :min="0"
              :max="1000"
              :step="5"
            />
            <span class="note">0-1000 次/天（全小院共享配额）</span>
          </el-form-item>
          <el-form-item label="引用来源上限">
            <el-input-number
              v-model="lab.max_sources"
              :min="0"
              :max="20"
            />
            <span class="note">0-20 条参考来源</span>
          </el-form-item>
        </el-form>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="生成结果始终先落为私有草稿，需经专家审核后才进入小院技能库。"
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

  const state = ref<ViewState>('loading')
  const saving = ref(false)

  const lab = reactive({
    enabled: true,
    daily_limit: 20,
    use_web: true,
    max_sources: 6,
  })

  async function loadSettings(): Promise<void> {
    state.value = 'loading'
    try {
      const data = await governanceApi.settings()
      Object.assign(lab, data.skill_lab)
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await governanceApi.updateSetting('skill_lab', {
        enabled: lab.enabled,
        use_web: lab.use_web,
        daily_limit: lab.daily_limit,
        max_sources: lab.max_sources,
      })
      ElMessage.success('技能生成配置已保存')
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
    gap: 12px;
    flex-wrap: wrap;
  }
  .settings-form {
    max-width: 760px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
