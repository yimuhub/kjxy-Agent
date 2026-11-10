<template>
  <AsyncState
    :state="state"
    empty-text="暂无语音转写配置"
    @retry="loadAll"
  >
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>语音转写通道</span>
          <el-button
            v-if="canManage"
            size="small"
            type="primary"
            :loading="saving"
            @click="saveMode"
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
        title="仅小院管理员可修改语音转写设置。"
        style="margin-bottom: 12px"
      />

      <el-form
        label-width="150px"
        class="settings-form"
      >
        <el-form-item label="使用方式">
          <el-radio-group
            v-model="mode"
            :disabled="!canManage"
          >
            <el-radio-button value="auto">
              自动（本机优先，在线兜底）
            </el-radio-button>
            <el-radio-button value="local">
              仅本机（离线）
            </el-radio-button>
            <el-radio-button value="online">
              仅在线
            </el-radio-button>
            <el-radio-button value="off">
              关闭
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="当前生效">
          <el-tag
            :type="effectiveTag.type"
            effect="dark"
          >
            {{ effectiveTag.label }}
          </el-tag>
          <span
            v-if="status"
            class="note"
          >{{ channelHint }}</span>
        </el-form-item>
        <el-form-item label="在线通道">
          <template v-if="status?.online.available">
            <el-tag type="success">
              可用
            </el-tag>
            <span class="note">
              {{ status.online.provider }} / {{ status.online.model }}
              <template v-if="status.online.explicit">（能力绑定）</template>
              <template v-else>（当前生效供应商兜底）</template>
            </span>
          </template>
          <template v-else>
            <el-tag type="info">
              未配置
            </el-tag>
            <span class="note">
              可在<router-link
                to="/model-center"
                class="link"
              >模型中心</router-link>的「语音转写」能力下绑定在线供应商
            </span>
          </template>
        </el-form-item>
        <el-form-item label="运行环境">
          <span
            v-if="status"
            class="note"
          >
            {{ status.platform.os }} / {{ status.platform.arch }} · 引擎
            {{ status.platform.engine }}
            <el-tag
              :type="status.platform.engine_available ? 'success' : 'warning'"
              size="small"
            >
              {{ status.platform.engine_available ? '已安装' : '缺失' }}
            </el-tag>
            · {{ status.platform.engine_note }}
          </span>
        </el-form-item>
      </el-form>

      <el-alert
        v-for="(warning, i) in status?.warnings ?? []"
        :key="i"
        type="warning"
        :closable="false"
        show-icon
        :title="warning"
        style="margin-top: 8px"
      />
    </el-card>

    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>本机模型</span>
          <span class="note">安装包不含模型，首次使用时按需下载（三端通用同一份 ONNX）</span>
        </div>
      </template>

      <div
        v-for="model in models"
        :key="model.id"
        class="model-row"
      >
        <div class="model-head">
          <div>
            <strong>{{ model.name }}</strong>
            <el-tag
              v-if="model.id === status?.settings.local_model_id"
              size="small"
              type="success"
              effect="plain"
            >
              当前使用
            </el-tag>
            <el-tag
              v-else-if="model.tier === 'high_accuracy'"
              size="small"
              type="warning"
              effect="plain"
            >
              高精度
            </el-tag>
            <el-tag
              v-else
              size="small"
              type="info"
              effect="plain"
            >
              轻量
            </el-tag>
          </div>
          <div class="model-actions">
            <el-button
              v-if="model.state.ready && model.id !== status?.settings.local_model_id"
              size="small"
              :disabled="!canManage"
              :loading="acting === model.id"
              @click="selectModel(model.id)"
            >
              设为当前
            </el-button>
            <el-button
              v-if="!model.state.ready && model.state.status === 'downloading'"
              size="small"
              @click="cancelDownload(model.id)"
            >
              取消
            </el-button>
            <el-button
              v-else-if="!model.state.ready"
              size="small"
              type="primary"
              :disabled="!canManage"
              :loading="acting === model.id"
              @click="startDownload(model.id)"
            >
              下载（{{ formatBytes(model.size_bytes) }}）
            </el-button>
            <el-button
              v-if="model.state.ready"
              size="small"
              type="danger"
              plain
              :disabled="!canManage"
              :loading="acting === model.id"
              @click="removeModel(model.id)"
            >
              删除
            </el-button>
          </div>
        </div>
        <p class="note model-desc">
          {{ model.description }}
        </p>
        <p class="note">
          体积 {{ formatBytes(model.size_bytes) }} · 内存 ≥ {{ model.min_ram_mb }}MB · 许可
          {{ model.license }}
        </p>
        <el-progress
          v-if="model.state.status === 'downloading' || model.state.status === 'verifying'"
          :percentage="model.state.progress"
          :stroke-width="10"
          :status="model.state.status === 'verifying' ? 'warning' : undefined"
        />
        <p
          v-if="model.state.message"
          class="note state-note"
          :class="{ error: model.state.status === 'error' || model.state.status === 'broken' }"
        >
          {{ model.state.message }}
          <template v-if="model.state.installed_bytes">
            · 已占用 {{ formatBytes(model.state.installed_bytes) }}
          </template>
        </p>
      </div>

      <el-divider />

      <div class="test-row">
        <el-button
          size="small"
          :loading="testing"
          :disabled="!canManage"
          @click="pickTestFile"
        >
          用一段音频测试当前通道
        </el-button>
        <input
          ref="testFileRef"
          type="file"
          accept="audio/wav,audio/webm,audio/ogg,audio/mpeg,.wav,.webm,.ogg,.mp3,.m4a"
          class="hidden-input"
          @change="onTestFile"
        >
        <span
          v-if="testResult"
          class="note"
        >
          识别结果：{{ testResult.text || '（空）' }} · {{ testResult.engine }} ·
          {{ testResult.model }}
        </span>
      </div>
    </el-card>
  </AsyncState>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import AsyncState from '@/components/AsyncState.vue'
  import { asrApi, type AsrMode, type AsrStatus, type AsrTestResult } from '@/api/asr'
  import { governanceApi } from '@/api/v3'
  import { useAuthStore } from '@/stores/auth'
  import { classifyError, type ViewState } from '@/utils/status'

  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())

  const state = ref<ViewState>('loading')
  const status = ref<AsrStatus | null>(null)
  const models = ref<AsrStatus['models']>([])
  const mode = ref<AsrMode>('auto')
  const saving = ref(false)
  const acting = ref('')
  const testing = ref(false)
  const testResult = ref<AsrTestResult | null>(null)
  const testFileRef = ref<HTMLInputElement | null>(null)
  let poll: ReturnType<typeof setInterval> | undefined
  let pollFailures = 0

  const EFFECTIVE_META: Record<string, { label: string; type: 'success' | 'info' | 'warning' }> = {
    local: { label: '本机离线', type: 'success' },
    online: { label: '在线通道', type: 'warning' },
    none: { label: '不可用', type: 'info' },
  }

  const effectiveTag = computed(() => {
    const key = status.value?.effective ?? 'none'
    return EFFECTIVE_META[key] ?? EFFECTIVE_META.none
  })

  const channelHint = computed(() => {
    const s = status.value
    if (!s) return ''
    if (s.effective === 'local') return '音频不出本机，离线也能用'
    if (s.effective === 'online') return '音频会上传到绑定的在线供应商'
    if (s.mode === 'off') return '成员在对话页点语音会提示先启用'
    return '需要下载本机模型或绑定在线供应商'
  })

  function formatBytes(value: number): string {
    if (!value) return '0MB'
    const mb = value / (1024 * 1024)
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)}GB` : `${mb.toFixed(0)}MB`
  }

  function stopPolling() {
    if (poll) clearInterval(poll)
    poll = undefined
    pollFailures = 0
  }

  function ensurePolling() {
    const busy = models.value.some(
      m => m.state.status === 'downloading' || m.state.status === 'verifying',
    )
    if (busy && !poll) {
      pollFailures = 0
      poll = setInterval(() => {
        void (async () => {
          try {
            await loadStatus()
            pollFailures = 0
          } catch {
            pollFailures += 1
            if (pollFailures >= 5) {
              stopPolling()
              state.value = 'error'
            }
          }
        })()
      }, 1500)
    } else if (!busy) {
      stopPolling()
    }
  }

  async function loadStatus() {
    const s = await asrApi.status()
    status.value = s
    models.value = s.models
    mode.value = s.mode
    state.value = 'ready'
    ensurePolling()
  }

  async function loadAll() {
    state.value = 'loading'
    try {
      await loadStatus()
    } catch (err) {
      state.value = classifyError(err)
    }
  }

  async function saveMode() {
    saving.value = true
    try {
      await governanceApi.updateSetting('asr', {
        mode: mode.value,
        local_model_id: status.value?.settings.local_model_id ?? '',
      })
      ElMessage.success('语音转写设置已保存')
      await loadStatus()
    } catch {
      /* 全局拦截器已提示 */
    } finally {
      saving.value = false
    }
  }

  async function startDownload(modelId: string) {
    acting.value = modelId
    try {
      await asrApi.startDownload(modelId)
      ElMessage.info('开始下载本机模型，完成后自动可用')
      await loadStatus()
    } catch {
      /* 全局拦截器已提示 */
    } finally {
      acting.value = ''
    }
  }

  async function selectModel(modelId: string) {
    acting.value = modelId
    try {
      await governanceApi.updateSetting('asr', { mode: mode.value, local_model_id: modelId })
      ElMessage.success('已切换本机模型')
      await loadStatus()
    } catch {
      /* 全局拦截器已提示 */
    } finally {
      acting.value = ''
    }
  }

  async function cancelDownload(modelId: string) {
    try {
      await asrApi.cancelDownload(modelId)
      await loadStatus()
    } catch {
      /* 全局拦截器已提示 */
    }
  }

  async function removeModel(modelId: string) {
    try {
      await ElMessageBox.confirm(
        '删除后需要重新下载才能离线转写；在线通道不受影响。',
        '删除本机模型',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    acting.value = modelId
    try {
      await asrApi.remove(modelId)
      ElMessage.success('已删除本机模型')
      await loadStatus()
    } catch {
      /* 全局拦截器已提示 */
    } finally {
      acting.value = ''
    }
  }

  function pickTestFile() {
    testResult.value = null
    testFileRef.value?.click()
  }

  async function onTestFile(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    testing.value = true
    try {
      testResult.value = await asrApi.test(file)
      if (!testResult.value.ok) ElMessage.warning('没有识别到内容，请换一段更清晰的音频')
    } catch {
      /* 全局拦截器已提示 */
    } finally {
      testing.value = false
    }
  }

  onMounted(loadAll)
  onBeforeUnmount(stopPolling)
</script>

<style scoped>
  .section + .section {
    margin-top: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .model-row {
    padding: 8px 0;
  }
  .model-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .model-head strong {
    margin-right: 8px;
  }
  .model-actions {
    display: flex;
    gap: 8px;
  }
  .model-desc {
    margin: 6px 0 2px;
    margin-left: 0;
  }
  .state-note {
    margin-left: 0;
  }
  .state-note.error {
    color: var(--ds-danger);
  }
  .test-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .test-row .note {
    margin-left: 0;
  }
  .hidden-input {
    display: none;
  }
  .link {
    color: var(--ds-accent);
  }
</style>
