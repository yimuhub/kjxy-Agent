<template>
  <el-dialog
    :model-value="modelValue"
    title="申请提升额度"
    width="520px"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert
      v-if="block"
      type="warning"
      :closable="false"
      show-icon
      class="block-alert"
    >
      {{ scopeText }}已用 {{ formatValue(block.used) }} / {{ formatValue(block.limit) }}，
      提交申请后由小院管理员审批，通过后额度自动生效。
    </el-alert>
    <el-form label-width="96px">
      <el-form-item label="期望额度">
        <el-input-number
          v-model="requestedLimit"
          :min="1"
          :step="step"
          style="width: 200px"
        />
        <span class="hint">{{ unitText }}</span>
      </el-form-item>
      <el-form-item label="有效期">
        <el-select
          v-model="validDays"
          style="width: 200px"
        >
          <el-option
            label="3 天"
            :value="3"
          />
          <el-option
            label="7 天"
            :value="7"
          />
          <el-option
            label="30 天"
            :value="30"
          />
        </el-select>
        <span class="hint">到期自动失效</span>
      </el-form-item>
      <el-form-item label="申请理由">
        <el-input
          v-model="reason"
          type="textarea"
          :rows="3"
          maxlength="500"
          placeholder="例如：本月底集中出诊断报告，需要临时提升额度"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">
        取消
      </el-button>
      <el-button
        type="primary"
        :loading="submitting"
        @click="submit"
      >
        提交申请
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import { quotaApi, type QuotaBlockInfo } from '@/api/quota'

  const props = defineProps<{
    modelValue: boolean
    block: QuotaBlockInfo | null
  }>()
  const emit = defineEmits<{ 'update:modelValue': [boolean]; submitted: [] }>()

  const requestedLimit = ref(0)
  const validDays = ref(7)
  const reason = ref('')
  const submitting = ref(false)

  const scopeText = computed(() => {
    const b = props.block
    if (!b) return ''
    const scope = { yard: '全小院', agent: '当前助手', agent_group: '该组助手', model: '该模型' }[b.scopeType] ?? '当前'
    const key = b.scopeKey && b.scopeType !== 'yard' ? `「${b.scopeKey}」` : ''
    return `${scope}${key}的${b.metric === 'tokens' ? 'Token ' : b.metric === 'cost' ? '费用' : '调用次数'}额度`
  })

  const unitText = computed(() => (props.block?.metric === 'tokens' ? 'Token' : props.block?.metric === 'cost' ? '元' : '次'))
  const step = computed(() => (props.block?.metric === 'tokens' ? 100_000 : props.block?.metric === 'cost' ? 10 : 100))

  function formatValue(value: number): string {
    const metric = props.block?.metric
    if (metric === 'cost') return `¥${Number(value).toFixed(2)}`
    if (metric === 'tokens') {
      if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
      if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
    }
    return String(Math.round(value))
  }

  watch(
    () => props.modelValue,
    open => {
      if (open && props.block) {
        requestedLimit.value = Math.max(Math.ceil(props.block.limit * 2), props.block.limit + 1)
        validDays.value = 7
        reason.value = ''
      }
    },
  )

  async function submit() {
    if (!props.block?.policyId) {
      ElMessage.error('缺少额度策略信息，请到「用量额度」页调整')
      return
    }
    submitting.value = true
    try {
      await quotaApi.createRequest({
        policy_id: props.block.policyId,
        requested_limit: requestedLimit.value,
        reason: reason.value,
        valid_days: validDays.value,
      })
      ElMessage.success('申请已提交，等待小院管理员审批')
      emit('update:modelValue', false)
      emit('submitted')
    } catch (e) {
      ElMessage.error('提交失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      submitting.value = false
    }
  }
</script>

<style scoped>
  .block-alert {
    margin-bottom: 14px;
  }
  .hint {
    margin-left: 10px;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
</style>
