<template>
  <div class="settings-self-improve">
    <AsyncState
      :state="state"
      empty-text="暂无自我总结配置"
      @retry="loadAll"
    >
      <!-- 设置表单（仅 can_manage 时显示编辑） -->
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>自我总结提升</span>
            <el-button
              v-if="report?.can_manage"
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
          v-if="!report?.can_manage"
          type="info"
          :closable="false"
          show-icon
          title="仅负责人或获得授权的管理员可修改此设置。"
          style="margin-bottom: 12px"
        />

        <el-form
          label-width="140px"
          class="settings-form"
        >
          <el-form-item label="启用自我总结">
            <el-switch
              v-model="si.enabled"
              :disabled="!canManage"
            />
            <span class="note">默认关闭；开启后按周期自动归纳窗口数据</span>
          </el-form-item>
          <el-form-item label="执行周期">
            <el-select
              v-model="si.schedule.preset"
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
            <template
              v-if="si.schedule.preset === 'custom'"
            >
              <el-input-number
                v-model="si.schedule.custom_value"
                :min="1"
                :max="720"
                :disabled="!canManage"
                style="width: 80px; margin: 0 8px"
              />
              <el-select
                v-model="si.schedule.custom_unit"
                :disabled="!canManage"
                style="width: 80px"
              >
                <el-option
                  label="小时"
                  value="hours"
                />
                <el-option
                  label="天"
                  value="days"
                />
                <el-option
                  label="月"
                  value="months"
                />
              </el-select>
            </template>
          </el-form-item>
          <el-form-item label="归纳范围">
            <el-radio-group
              v-model="si.scope"
              :disabled="!canManage"
            >
              <el-radio value="per_member">
                按成员
              </el-radio>
              <el-radio value="yard">
                小院整体
              </el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="每日 token 预算">
            <el-input-number
              v-model="si.daily_budget_tokens"
              :min="1000"
              :max="10000000"
              :step="10000"
              :disabled="!canManage"
            />
            <span class="note">超出后自动跳过，明天恢复</span>
          </el-form-item>
          <el-form-item label="保留天数">
            <el-input-number
              v-model="si.retain_days"
              :min="7"
              :max="365"
              :step="7"
              :disabled="!canManage"
            />
            <span class="note">运行台账与旧记忆保留期限</span>
          </el-form-item>
          <el-form-item label="自动通过知识候选">
            <el-switch
              v-model="si.auto_publish"
              :disabled="!canManage"
            />
            <span class="note">技能提案仍保留人工审核</span>
          </el-form-item>
          <el-form-item label="通知负责人">
            <el-switch
              v-model="si.notify_owner"
              :disabled="!canManage"
            />
          </el-form-item>
        </el-form>
      </el-card>

      <!-- 运行报告 -->
      <el-card
        v-if="report"
        class="section"
      >
        <template #header>
          <div class="card-header">
            <span>运行状态</span>
            <el-button
              v-if="report.can_manage"
              size="small"
              :loading="running"
              @click="runNow"
            >
              立即运行一次
            </el-button>
          </div>
        </template>

        <el-descriptions
          :column="3"
          border
        >
          <el-descriptions-item label="任务状态">
            <el-tag :type="jobTagType">
              {{ jobStatusLabel }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="下次运行">
            {{ report.job.next_run_at ? new Date(report.job.next_run_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="上次运行">
            {{ report.job.last_run_at ? new Date(report.job.last_run_at).toLocaleString() : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="今日 token 用量">
            {{ report.budget.used_today }} / {{ report.budget.daily_budget_tokens }}
          </el-descriptions-item>
          <el-descriptions-item label="剩余预算">
            {{ report.budget.remaining }}
          </el-descriptions-item>
          <el-descriptions-item label="上次结果">
            <template v-if="report.last_run">
              <el-tag
                :type="runTagType(report.last_run.status)"
                size="small"
              >
                {{ report.last_run.status }}
              </el-tag>
              <span
                v-if="report.last_run.duration_ms != null"
                class="note"
              >
                {{ report.last_run.duration_ms }}ms
              </span>
            </template>
            <template v-else>
              —
            </template>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 运行历史 -->
        <el-table
          v-if="runs.length"
          :data="runs"
          size="small"
          style="margin-top: 12px"
          max-height="240"
        >
          <el-table-column
            label="时间"
            width="160"
          >
            <template #default="{ row }">
              {{ row.started_at ? new Date(row.started_at).toLocaleString() : '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="触发"
            width="60"
            prop="trigger"
          />
          <el-table-column
            label="状态"
            width="80"
          >
            <template #default="{ row }">
              <el-tag
                :type="runTagType(row.status)"
                size="small"
              >
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="Tokens"
            width="80"
            prop="tokens_used"
          />
          <el-table-column
            label="耗时(ms)"
            width="80"
            prop="duration_ms"
          />
          <el-table-column
            label="备注"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.output?.note || row.error || '—' }}
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </AsyncState>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { governanceApi, selfImproveApi, type SelfImproveReport, type SelfImproveRun } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const state = ref<ViewState>('loading')
  const saving = ref(false)
  const running = ref(false)
  const report = ref<SelfImproveReport | null>(null)
  const runs = ref<SelfImproveRun[]>([])

  const si = reactive({
    enabled: false,
    schedule: { preset: 'daily', custom_value: 1, custom_unit: 'days' },
    scope: 'per_member' as 'yard' | 'per_member',
    daily_budget_tokens: 200000,
    auto_publish: false,
    retain_days: 90,
    notify_owner: true,
  })

  const presetOptions = [
    { value: 'hours6', label: '每 6 小时' },
    { value: 'hours12', label: '每 12 小时' },
    { value: 'daily', label: '每天' },
    { value: 'days3', label: '每 3 天' },
    { value: 'weekly', label: '每周' },
    { value: 'monthly', label: '每月' },
    { value: 'custom', label: '自定义' },
  ]

  const canManage = computed(() => report.value?.can_manage ?? false)

  const jobTagType = computed(() => {
    const s = report.value?.job.status
    if (s === 'active') return 'success'
    if (s === 'paused') return 'info'
    return 'warning'
  })
  const jobStatusLabel = computed(() => {
    const s = report.value?.job.status
    if (s === 'active') return '已排期'
    if (s === 'paused') return '已暂停'
    return '未创建'
  })

  function runTagType(status: string): 'success' | 'danger' | 'warning' | 'info' {
    if (status === 'success') return 'success'
    if (status === 'failed') return 'danger'
    if (status === 'skipped') return 'warning'
    return 'info'
  }

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    try {
      const [rep, runsData] = await Promise.all([
        selfImproveApi.report().catch(() => null),
        selfImproveApi.runs(10).catch(() => ({ items: [] as SelfImproveRun[] })),
      ])
      if (rep) {
        report.value = rep
        const s = rep.settings as typeof si
        Object.assign(si, s)
      }
      runs.value = runsData.items
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      const payload: Record<string, unknown> = {
        enabled: si.enabled,
        schedule: si.schedule,
        scope: si.scope,
        daily_budget_tokens: si.daily_budget_tokens,
        auto_publish: si.auto_publish,
        retain_days: si.retain_days,
        notify_owner: si.notify_owner,
      }
      if (si.auto_publish) {
        // 二次确认
        await ElMessageBox.confirm(
          '开启自动通过知识候选后，系统生成的知识将自动审核入库（技能提案仍需人工审核）。确认开启？',
          '确认自动发布',
          { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' },
        )
        ;(payload as Record<string, unknown>).confirm_auto_publish = true
      }
      await governanceApi.updateSetting('self_improve', payload)
      ElMessage.success('自我总结设置已保存')
      await loadAll()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  async function runNow(): Promise<void> {
    running.value = true
    try {
      const result = await selfImproveApi.run()
      ElMessage.success(`运行完成：${result.status}`)
      await loadAll()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '运行失败'
      ElMessage.error(msg)
    } finally {
      running.value = false
    }
  }

  onMounted(() => {
    void loadAll()
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
