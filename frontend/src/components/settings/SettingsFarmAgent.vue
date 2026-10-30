<template>
  <AsyncState
    :state="state"
    empty-text="暂无自动执行配置"
    @retry="loadAll"
  >
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>Agent 自动执行</span>
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
        title="仅小院管理员可修改自动执行设置。"
        style="margin-bottom: 12px"
      />

      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item label="启用自动执行">
          <el-switch
            v-model="form.enabled"
            :disabled="!canManage"
          />
          <span class="note">按周期读设备/天气/台账，自动出预警并执行动作；未启用也可在台账页手动跑一轮</span>
        </el-form-item>
        <el-form-item label="执行周期">
          <el-select
            v-model="form.schedule.preset"
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
            v-model="form.window_hours"
            :min="6"
            :max="168"
            :step="6"
            :disabled="!canManage"
          />
          <span class="note">小时（默认 24）</span>
        </el-form-item>
        <el-form-item label="降噪时长">
          <el-input-number
            v-model="form.dedupe_hours"
            :min="1"
            :max="168"
            :disabled="!canManage"
          />
          <span class="note">小时；同一发现在此窗口内不重复产出动作，避免刷屏</span>
        </el-form-item>
        <el-form-item label="模型总结">
          <el-switch
            v-model="form.explain_with_llm"
            :disabled="!canManage"
          />
          <span class="note">只把规则发现写成一段结论；动作仍全部由规则产生</span>
        </el-form-item>
        <el-form-item label="每日 token 预算">
          <el-input-number
            v-model="form.daily_budget_tokens"
            :min="0"
            :max="10000000"
            :step="10000"
            :disabled="!canManage"
          />
          <span class="note">超出预算自动跳过模型总结，规则结论照常</span>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">
        动作策略
      </el-divider>
      <p class="note block">
        自动：直接写入并推送；待确认：先进入台账页「待确认」，由人点「执行」；关闭：不产出该动作。
        告警与系统观察记录建议自动，工单/诊断/药剂/地块建议待确认。
      </p>
      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item
          v-for="item in ACTION_ITEMS"
          :key="item.kind"
          :label="item.label"
        >
          <el-select
            v-model="form.actions[item.kind]"
            :disabled="!canManage"
            style="width: 160px"
          >
            <el-option
              label="自动执行"
              value="auto"
            />
            <el-option
              label="待人工确认"
              value="confirm"
            />
            <el-option
              label="关闭"
              value="off"
            />
          </el-select>
          <span class="note">{{ item.hint }}</span>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">
        预警阈值
      </el-divider>
      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item
          v-for="item in THRESHOLD_ITEMS"
          :key="item.key"
          :label="item.label"
        >
          <el-input-number
            v-model="form.thresholds[item.key]"
            :min="item.min"
            :max="item.max"
            :step="item.step"
            :disabled="!canManage"
          />
          <span class="note">{{ item.unit }} · {{ item.hint }}</span>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">
        天气预报（可选）
      </el-divider>
      <el-form
        label-width="170px"
        class="settings-form"
      >
        <el-form-item label="供应商">
          <el-select
            v-model="form.weather_provider"
            :disabled="!canManage"
            style="width: 200px"
          >
            <el-option
              label="不接入（只用设备实况）"
              value="none"
            />
            <el-option
              label="和风天气"
              value="qweather"
            />
            <el-option
              label="OpenWeather"
              value="openweather"
            />
          </el-select>
        </el-form-item>
        <template v-if="form.weather_provider !== 'none'">
          <el-form-item label="API Key">
            <el-input
              v-model="form.weather_api_key"
              type="password"
              show-password
              :disabled="!canManage"
              :placeholder="apiKeyConfigured ? '已配置，留空表示不修改' : '填写供应商 API Key'"
              style="width: 320px"
            />
          </el-form-item>
          <el-form-item :label="form.weather_provider === 'qweather' ? 'Location ID / 城市' : '城市名（可选）'">
            <el-input
              v-model="form.weather_location"
              :disabled="!canManage"
              style="width: 260px"
            />
          </el-form-item>
          <el-form-item
            v-if="form.weather_provider === 'openweather'"
            label="经纬度（可选）"
          >
            <el-input-number
              v-model="form.weather_lat"
              :precision="4"
              :min="-90"
              :max="90"
              :disabled="!canManage"
            />
            <el-input-number
              v-model="form.weather_lon"
              :precision="4"
              :min="-180"
              :max="180"
              :disabled="!canManage"
              style="margin-left: 8px"
            />
            <span class="note">填经纬度优先于城市名</span>
          </el-form-item>
        </template>
      </el-form>

      <p class="note boundary">
        边界：规则先行、证据可追溯；设备未绑定地块时只出告警不自动诊断；药剂只按台账里出现的名称建草稿，
        不猜有效成分与安全间隔期；用药相关建议仍需走专家复核。
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

  type Policy = 'auto' | 'confirm' | 'off'
  type ActionKind = 'alert' | 'work_order' | 'diagnosis' | 'record' | 'pesticide' | 'plot'

  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())
  const state = ref<ViewState>('loading')
  const saving = ref(false)
  const apiKeyConfigured = ref(false)

  const ACTION_ITEMS: { kind: ActionKind; label: string; hint: string }[] = [
    { kind: 'alert', label: '告警', hint: '写入「告警与工单」并推送' },
    { kind: 'work_order', label: '工单', hint: '生成灌溉/防冻/维护等作业工单' },
    { kind: 'diagnosis', label: '作物诊断', hint: '引用设备自动发起诊断（需地块与作物）' },
    { kind: 'record', label: '台账观察记录', hint: '把系统观察写成巡田记录（source=agent）' },
    { kind: 'pesticide', label: '药剂登记', hint: '按用药记录里的名称建草稿，待补充标签' },
    { kind: 'plot', label: '地块建档', hint: '有设备无地块时建议建档' },
  ]

  type ThresholdKey =
    | 'soil_moisture_low'
    | 'rain_no_spray_mm'
    | 'wind_no_spray_ms'
    | 'temp_drop_c'
    | 'temp_min_c'
    | 'temp_max_c'
    | 'humidity_disease_pct'
    | 'offline_hours'

  const THRESHOLD_ITEMS: {
    key: ThresholdKey
    label: string
    unit: string
    min: number
    max: number
    step: number
    hint: string
  }[] = [
    { key: 'soil_moisture_low', label: '土壤水分下限', unit: '%', min: 1, max: 80, step: 1, hint: '低于该值且无有效降雨' },
    { key: 'rain_no_spray_mm', label: '降雨作业阈值', unit: 'mm/24h', min: 0, max: 200, step: 1, hint: '超过则提示不宜施药' },
    { key: 'wind_no_spray_ms', label: '风速作业阈值', unit: 'm/s', min: 0, max: 40, step: 0.5, hint: '超过则提示飘移风险' },
    { key: 'temp_drop_c', label: '降温幅度', unit: '℃', min: 0, max: 30, step: 0.5, hint: '较当前降温超过则预警' },
    { key: 'temp_min_c', label: '最低温阈值', unit: '℃', min: -30, max: 20, step: 0.5, hint: '未来最低温低于则预警' },
    { key: 'temp_max_c', label: '最高温阈值', unit: '℃', min: 15, max: 55, step: 0.5, hint: '未来最高温高于则预警' },
    { key: 'humidity_disease_pct', label: '病害湿度阈值', unit: '%RH', min: 50, max: 100, step: 1, hint: '高湿+低温组合判病害风险' },
    { key: 'offline_hours', label: '离线判定', unit: '小时', min: 0.5, max: 168, step: 0.5, hint: '无上报超过则提醒维护' },
  ]

  const presetOptions = [
    { value: 'hours6', label: '每 6 小时' },
    { value: 'hours12', label: '每 12 小时' },
    { value: 'daily', label: '每天' },
  ]

  const form = reactive({
    enabled: false,
    schedule: { preset: 'daily' },
    window_hours: 24,
    dedupe_hours: 24,
    explain_with_llm: true,
    daily_budget_tokens: 100000,
    actions: {
      alert: 'auto' as Policy,
      work_order: 'confirm' as Policy,
      diagnosis: 'confirm' as Policy,
      record: 'auto' as Policy,
      pesticide: 'confirm' as Policy,
      plot: 'confirm' as Policy,
    },
    thresholds: {
      soil_moisture_low: 20,
      rain_no_spray_mm: 10,
      wind_no_spray_ms: 10,
      temp_drop_c: 8,
      temp_min_c: 3,
      temp_max_c: 35,
      humidity_disease_pct: 85,
      offline_hours: 6,
    },
    weather_provider: 'none',
    weather_api_key: '',
    weather_location: '',
    weather_lat: null as number | null,
    weather_lon: null as number | null,
  })

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    try {
      const bundle = await governanceApi.settings()
      const current = (bundle as unknown as Record<string, Record<string, unknown>>).farm_agent
      if (current) {
        form.enabled = Boolean(current.enabled)
        form.schedule.preset = String((current.schedule as { preset?: string })?.preset || 'daily')
        form.window_hours = Number(current.window_hours ?? 24)
        form.dedupe_hours = Number(current.dedupe_hours ?? 24)
        form.explain_with_llm = Boolean(current.explain_with_llm)
        form.daily_budget_tokens = Number(current.daily_budget_tokens ?? 100000)
        Object.assign(form.actions, (current.actions as Record<string, Policy>) || {})
        Object.assign(form.thresholds, (current.thresholds as Record<string, number>) || {})
        form.weather_provider = String(current.weather_provider || 'none')
        form.weather_location = String(current.weather_location || '')
        form.weather_lat = (current.weather_lat as number | null) ?? null
        form.weather_lon = (current.weather_lon as number | null) ?? null
        apiKeyConfigured.value = Boolean(current.weather_api_key_configured)
        form.weather_api_key = ''
      }
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function save(): Promise<void> {
    saving.value = true
    try {
      const payload: Record<string, unknown> = {
        enabled: form.enabled,
        schedule: { preset: form.schedule.preset },
        window_hours: form.window_hours,
        dedupe_hours: form.dedupe_hours,
        explain_with_llm: form.explain_with_llm,
        daily_budget_tokens: form.daily_budget_tokens,
        actions: { ...form.actions },
        thresholds: { ...form.thresholds },
        weather_provider: form.weather_provider,
        weather_location: form.weather_location,
        weather_lat: form.weather_lat,
        weather_lon: form.weather_lon,
      }
      if (form.weather_api_key) payload.weather_api_key = form.weather_api_key
      await governanceApi.updateSetting('farm_agent', payload)
      ElMessage.success('自动执行设置已保存')
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
  .block {
    display: block;
    margin: 0 0 8px;
    line-height: 1.7;
  }
  .boundary {
    margin: 12px 0 0;
    line-height: 1.7;
  }
</style>
