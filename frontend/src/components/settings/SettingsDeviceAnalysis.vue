<template>
  <AsyncState
    :state="state"
    empty-text="暂无设备分析配置"
    @retry="loadAll"
  >
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>设备分析</span>
          <el-button
            v-if="canManage"
            size="small"
            type="primary"
            :loading="saving"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="!canManage"
        type="info"
        :closable="false"
        show-icon
        title="仅小院管理员可修改设备分析设置。"
        style="margin-bottom: 12px"
      />

      <el-form
        label-width="150px"
        class="settings-form"
      >
        <el-form-item label="启用每日分析">
          <el-switch
            v-model="da.enabled"
            :disabled="!canManage"
          />
          <span class="note">有启用设备的小院按周期自动分析；也可在设备卡片点「立即分析」</span>
        </el-form-item>
        <el-form-item label="执行周期">
          <el-select
            v-model="da.schedule.preset"
            :disabled="!canManage"
            style="width: 160px"
          >
            <el-option
              v-for="opt in presetOptions"
              :key="opt.value"
              :label="opt.label"
              :value="opt.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="分析窗口">
          <el-input-number
            v-model="da.window_hours"
            :min="6"
            :max="168"
            :step="6"
            :disabled="!canManage"
          />
          <span class="note">小时（默认 24）</span>
        </el-form-item>
        <el-form-item label="模型解释">
          <el-switch
            v-model="da.explain_with_llm"
            :disabled="!canManage"
          />
          <span class="note">关闭后只出规则结论；阈值/趋势/组合发现与 critical 告警不受影响</span>
        </el-form-item>
        <el-form-item label="每日 token 预算">
          <el-input-number
            v-model="da.daily_budget_tokens"
            :min="0"
            :max="10000000"
            :step="10000"
            :disabled="!canManage"
          />
          <span class="note">超出预算自动只出规则结论，明天恢复</span>
        </el-form-item>
      </el-form>

      <p class="note boundary">
        边界：分析只读设备读数，不控制设备；结论引用具体数值与时间，证据不足时明确说「证据不足」；
        用药类建议仍需走作物诊断与专家复核。
      </p>
    </el-card>
  </AsyncState>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'
  import { useAuthStore } from '@/stores/auth'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())
  const state = ref<ViewState>('loading')
  const saving = ref(false)

  const da = reactive({
    enabled: true,
    schedule: { preset: 'daily' },
    explain_with_llm: true,
    daily_budget_tokens: 100000,
    window_hours: 24,
  })

  const presetOptions = [
    { value: 'hours12', label: '每 12 小时' },
    { value: 'daily', label: '每天' },
    { value: 'weekly', label: '每周' },
  ]

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    try {
      const bundle = await governanceApi.settings()
      const current = bundle.device_analysis
      if (current) {
        da.enabled = Boolean(current.enabled)
        da.schedule.preset = current.schedule?.preset || 'daily'
        da.explain_with_llm = Boolean(current.explain_with_llm)
        da.daily_budget_tokens = Number(current.daily_budget_tokens ?? 100000)
        da.window_hours = Number(current.window_hours ?? 24)
      }
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      await governanceApi.updateSetting('device_analysis', {
        enabled: da.enabled,
        schedule: { preset: da.schedule.preset },
        explain_with_llm: da.explain_with_llm,
        daily_budget_tokens: da.daily_budget_tokens,
        window_hours: da.window_hours,
      })
      ElMessage.success('设备分析设置已保存')
      await loadAll()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  onMounted(loadAll)
</script>

<style scoped>
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .boundary {
    margin: 12px 0 0;
    line-height: 1.7;
  }
</style>
