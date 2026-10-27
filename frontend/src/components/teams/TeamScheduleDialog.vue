<template>
  <el-dialog
    v-model="visible"
    :title="`定时任务：${team?.name ?? ''}`"
    width="min(580px, 94vw)"
    :close-on-click-modal="!saving"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    @open="reset"
  >
    <el-alert
      title="按间隔周期运行，不是指定时刻。系统每 5 分钟检查一次，到期后排队执行。默认停用。"
      type="info"
      :closable="false"
      show-icon
    />
    <p class="note">
      {{ team?.schedule?.enabled && team.enabled ? `下次运行：${formatTime(team.next_run_at)}` : '定时任务已停用或班组已停用' }}
    </p>
    <p
      v-if="!canManage"
      class="note"
    >
      仅管理员可以编辑定时任务。
    </p>
    <el-form
      :model="form"
      label-width="96px"
      :disabled="!canManage || saving"
      @submit.prevent="save"
    >
      <el-form-item label="启用任务">
        <el-switch v-model="form.enabled" />
      </el-form-item>
      <el-form-item
        label="间隔分钟"
        required
      >
        <el-input-number
          v-model="form.interval_minutes"
          :min="5"
          :max="43200"
          :step="5"
          :precision="0"
        />
        <span class="note range">5–43200 分钟</span>
      </el-form-item>
      <el-form-item
        label="运行问题"
        :required="form.enabled"
      >
        <el-input
          v-model="form.input"
          type="textarea"
          :rows="4"
          maxlength="8000"
          show-word-limit
          placeholder="每次定时运行交给班组处理的问题"
        />
      </el-form-item>
      <el-form-item label="结果格式">
        <el-select v-model="form.output_format">
          <el-option
            label="文字答案"
            value="text"
          />
          <el-option
            label="Markdown 文档"
            value="md"
          />
          <el-option
            label="Word 文档"
            value="docx"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <template #footer>
      <el-button
        :disabled="saving"
        @click="visible = false"
      >
        关闭
      </el-button>
      <el-button
        v-if="canManage"
        type="primary"
        :loading="saving"
        @click="save"
      >
        保存定时任务
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi, type TeamDefinition, type TeamSchedule } from '@/api/modules'
import { formatTime } from '@/utils/format'
import { scheduleError } from './presentation'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ team: TeamDefinition | null; canManage: boolean }>()
const emit = defineEmits<{ saved: [] }>()
const form = reactive<TeamSchedule>({ enabled: false, interval_minutes: 1440, input: '', output_format: 'text' })
const saving = ref(false)
const error = ref('')
function reset() {
  Object.assign(form, props.team?.schedule ?? { enabled: false, interval_minutes: 1440, input: '', output_format: 'text' })
  error.value = ''
}
async function save() {
  if (!props.canManage || !props.team || saving.value) return
  error.value = scheduleError(form)
  if (error.value) return
  saving.value = true
  try {
    await teamApi.schedule(props.team.id, { ...form, input: form.input.trim() })
    ElMessage.success(form.enabled ? '定时任务已启用' : '定时任务已停用')
    visible.value = false
    emit('saved')
  } catch {
    error.value = '定时任务保存失败，请检查权限或连接后重试。'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.note { color: var(--ds-text-3); font-size: 12px; line-height: 1.6; }
.range { margin-left: 8px; }
</style>
