<template>
  <el-dialog
append-to-body
    :model-value="modelValue"
    title="上架前评测与签名"
    width="720px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
    @open="load"
  >
    <div
      v-if="loading"
      class="eval-loading"
    >
      正在读取评测报告…
    </div>
    <el-alert
      v-else-if="errorText"
      type="error"
      :title="errorText"
      :closable="false"
      show-icon
    />
    <template v-else-if="attestation">
      <section class="attest-head">
        <div>
          <strong>{{ skillName }}</strong>
          <span class="muted"> v{{ attestation.version }}</span>
        </div>
        <el-tag
          :type="verifyMeta.type"
          effect="dark"
          size="small"
        >
          {{ verifyMeta.label }}
        </el-tag>
      </section>

      <el-descriptions
        :column="2"
        border
        size="small"
        class="attest-desc"
      >
        <el-descriptions-item label="定义摘要">
          <code :title="attestation.definition_sha256 || ''">
            {{ shortDigest(attestation.definition_sha256) }}
          </code>
        </el-descriptions-item>
        <el-descriptions-item label="签名算法">
          {{ attestation.signature_alg || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="签名密钥">
          {{ attestation.key_id || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="签名时间">
          {{ attestation.signed_at ? formatDateTime(attestation.signed_at) : '—' }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="attestation.verification.reason !== 'ok'"
          label="校验说明"
          :span="2"
        >
          {{ verifyMeta.reason }}
        </el-descriptions-item>
      </el-descriptions>

      <template v-if="report">
        <h4 class="eval-title">
          最近一次评测
          <span class="muted">
            {{ reportKindLabel }} · {{ formatDateTime(attestation.latest_eval_run?.created_at) }}
          </span>
        </h4>
        <div class="eval-summary">
          <el-tag
            :type="report.pass ? 'success' : 'danger'"
            effect="plain"
          >
            {{ report.pass ? '通过' : '未通过' }}
          </el-tag>
          <span>静态得分 <strong>{{ report.score }}</strong> / 100</span>
          <span>致命项 {{ report.errors }}</span>
          <span>提示项 {{ report.warnings }}</span>
          <span
            v-if="report.dataset"
            class="muted"
          >
            数据集 {{ report.dataset.test_set_id }}：通过率
            {{ percent(report.dataset.pass_rate) }}（要求
            {{ percent(report.dataset.minimum_pass_rate) }}）
          </span>
        </div>

        <el-table
          :data="sortedChecks"
          size="small"
          max-height="260"
        >
          <el-table-column
            label="检查项"
            width="180"
          >
            <template #default="{ row }">
              {{ checkLabel(row.code) }}
            </template>
          </el-table-column>
          <el-table-column
            label="级别"
            width="90"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.severity === 'error' ? 'danger' : 'warning'"
                effect="plain"
              >
                {{ row.severity === 'error' ? '致命' : '提示' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="结果"
            width="80"
          >
            <template #default="{ row }">
              {{ row.passed ? '通过' : '不通过' }}
            </template>
          </el-table-column>
          <el-table-column
            label="说明"
            min-width="240"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.detail }}
            </template>
          </el-table-column>
        </el-table>

        <div
          v-if="failedCases.length"
          class="failed-cases"
        >
          <strong>未通过用例：</strong>
          <el-tag
            v-for="caseId in failedCases"
            :key="caseId"
            size="small"
            type="danger"
            effect="plain"
          >
            {{ caseId }}
          </el-tag>
        </div>
        <p
          v-if="report.notes?.length"
          class="muted notes"
        >
          {{ report.notes.join('；') }}
        </p>
      </template>
      <el-alert
        v-else
        type="info"
        title="该版本还没有评测记录。发布或市场安装时会自动生成一份。"
        :closable="false"
        show-icon
      />
    </template>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">
        关闭
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { skillApi, skillApiErrorText, type SkillAttestation } from '@/api/v3'
  import type { Skill } from '@/types/api'
  import { formatDateTime } from '@/utils/format'
  import { skillName as skillNameOf } from '@/components/library/skillMeta'

  const props = defineProps<{
    modelValue: boolean
    skill: Skill | null
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void
  }>()

  const loading = ref(false)
  const errorText = ref('')
  const attestation = ref<SkillAttestation | null>(null)

  const skillName = computed(() => (props.skill ? skillNameOf(props.skill) : ''))

  const report = computed(() => attestation.value?.latest_eval_run?.report ?? null)

  const sortedChecks = computed(() => {
    const checks = report.value?.checks ?? []
    return [...checks].sort((a, b) => Number(a.passed) - Number(b.passed))
  })

  const failedCases = computed(() => report.value?.dataset?.failed_case_ids ?? [])

  const verifyMeta = computed(() => {
    const verification = attestation.value?.verification
    if (!verification || verification.reason === 'unsigned') {
      return {
        type: 'info' as const,
        label: '未签名',
        reason: '该版本发布时没有生成签名（3.17 之前的存量版本）。',
      }
    }
    if (verification.valid) {
      return { type: 'success' as const, label: '签名有效', reason: '' }
    }
    if (verification.reason === 'definition_mismatch') {
      return {
        type: 'danger' as const,
        label: '定义已被改动',
        reason: '当前定义与签名时的摘要不一致，发布后内容被修改过。',
      }
    }
    if (verification.reason === 'key_mismatch') {
      return {
        type: 'warning' as const,
        label: '签名密钥已轮换',
        reason: '签名使用的密钥与当前密钥不一致，需要重新发布生成新签名。',
      }
    }
    return {
      type: 'danger' as const,
      label: '签名无效',
      reason: '签名无法通过公钥校验。',
    }
  })

  const reportKindLabel = computed(() => {
    const kind = attestation.value?.latest_eval_run?.kind
    if (kind === 'gate') return '数据集门禁'
    if (kind === 'install') return '市场安装'
    if (kind === 'promote') return '负责人推广'
    return '静态质量检查'
  })

  const CHECK_LABELS: Record<string, string> = {
    definition_present: '定义完整',
    identity: '身份信息',
    prompt_or_steps: '提示词/步骤',
    prompt_size: '提示词长度',
    steps_count: '步骤数量',
    tool_steps_declared: '工具声明',
    high_risk_review: '高风险审核',
    citation_sources: '引用来源',
    eval_cases: '评例数量',
    license: '许可证',
    dataset_gate: '数据集通过率',
  }

  function checkLabel(code: string): string {
    return CHECK_LABELS[code] || code
  }

  function shortDigest(digest?: string | null): string {
    return digest ? `${digest.slice(0, 16)}…` : '—'
  }

  function percent(value?: number | null): string {
    return `${Math.round(Number(value ?? 0) * 100)}%`
  }

  async function load() {
    if (!props.skill) return
    loading.value = true
    errorText.value = ''
    attestation.value = null
    try {
      attestation.value = await skillApi.attestation(props.skill.id, { skipErrorToast: true })
    } catch (e) {
      errorText.value = skillApiErrorText(e, '评测报告')
    } finally {
      loading.value = false
    }
  }
</script>

<style scoped>
  .eval-loading {
    padding: 24px 0;
    color: var(--el-text-color-secondary);
  }

  .attest-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }

  .attest-desc {
    margin-bottom: 16px;
  }

  .eval-title {
    margin: 4px 0 8px;
    font-size: 14px;
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .eval-title .muted {
    font-size: 12px;
    font-weight: 400;
  }

  .eval-summary {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    margin-bottom: 10px;
    font-size: 13px;
  }

  .failed-cases {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    font-size: 13px;
  }

  .notes {
    margin-top: 8px;
    font-size: 12px;
  }

  .muted {
    color: var(--el-text-color-secondary);
  }
</style>
