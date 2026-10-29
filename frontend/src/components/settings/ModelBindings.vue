<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>{{ title }}</span>
        <div class="page-actions">
          <el-tag
            size="small"
            type="info"
            effect="plain"
          >
            主用 → 兜底：{{ entries.length }} / {{ maxFallback }}
          </el-tag>
          <el-button
            size="small"
            @click="load"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            :loading="saving"
            @click="save"
          >
            保存
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="hint"
      :title="hint"
    />

    <div
      v-for="(row, index) in entries"
      :key="index"
      class="entry-row"
    >
      <span class="entry-order">
        {{ index === 0 ? '主用' : `兜底 ${index}` }}
      </span>
      <el-select
        v-model="row.supplier_id"
        placeholder="选择供应商"
        class="entry-supplier"
      >
        <el-option
          v-for="s in suppliers"
          :key="s.id"
          :label="`${s.name}（${s.model_id}）`"
          :value="s.id"
          :disabled="s.status !== 1"
        />
      </el-select>
      <el-input
        v-model="row.model_name"
        placeholder="模型覆盖（留空=供应商默认）"
        class="entry-model"
        maxlength="100"
      />
      <div class="page-actions">
        <el-button
          size="small"
          text
          :disabled="index === 0"
          @click="move(index, -1)"
        >
          上移
        </el-button>
        <el-button
          size="small"
          text
          :disabled="index === entries.length - 1"
          @click="move(index, 1)"
        >
          下移
        </el-button>
        <el-button
          size="small"
          text
          type="danger"
          @click="entries.splice(index, 1)"
        >
          移除
        </el-button>
      </div>
    </div>

    <el-button
      size="small"
      :disabled="entries.length >= maxFallback"
      @click="addRow"
    >
      添加一档兜底
    </el-button>
    <span
      v-if="entries.length >= maxFallback"
      class="note"
    >同一层最多 {{ maxFallback }} 档，再多只会掩盖配置错误</span>

    <div
      v-if="resolution"
      class="resolution"
    >
      <span class="resolution-title">当前解析（这次会用谁）</span>
      <ol class="resolution-hops">
        <li
          v-for="(hop, i) in resolution.hops"
          :key="i"
          :class="{ 'is-head': i === 0 }"
        >
          <el-tag
            size="small"
            :type="i === 0 ? 'success' : 'info'"
            effect="plain"
          >
            {{ hop.level_label }}
          </el-tag>
          <span class="resolution-name">{{ hop.supplier_name }}</span>
          <code class="note">{{ hop.model_name }}</code>
          <span class="note">{{ hop.reason }}</span>
        </li>
      </ol>
      <p
        v-if="!resolution.hops.length"
        class="note"
      >
        当前没有可用供应商，调用会回落到环境变量配置（{{ 'settings 兜底' }}）。
      </p>
    </div>
  </el-card>
</template>

<script setup lang="ts">
  /** 同一绑定层的兜底顺序编辑器（v3.25 模型中心）：主用 + 最多 2 档兜底。 */
  import { onMounted, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import { llmBindingApi } from '@/api/modules'
  import type { LlmBindingEntry, LlmSupplier } from '@/api/modules'

  const props = defineProps<{
    scopeType: 'global' | 'purpose' | 'agent'
    scopeKey: string
    title: string
    hint: string
    suppliers: LlmSupplier[]
    maxFallback: number
    /** 解析预览按哪条链路展示（能力绑定传 purpose，全局传空） */
    previewPurpose?: string
  }>()

  const entries = ref<LlmBindingEntry[]>([])
  const saving = ref(false)
  const emit = defineEmits<{ (event: 'saved'): void }>()
  const resolution = ref<{ hops: { level_label: string; supplier_name: string | null; model_name: string | null; reason: string }[] } | null>(
    null,
  )

  async function load(): Promise<void> {
    try {
      const res = await llmBindingApi.list({
        scope_type: props.scopeType,
        scope_key: props.scopeKey,
      })
      entries.value = res.items.map(item => ({
        supplier_id: item.supplier_id,
        model_name: item.model_name ?? '',
        priority: item.priority,
        enabled: item.enabled,
      }))
    } catch {
      entries.value = []
    }
    await loadResolution()
  }

  async function loadResolution(): Promise<void> {
    // 预览必须按「这一层」解析：能力绑定带 purpose、助手绑定带 agent_code，
    // 否则弹窗里列出的是无关链路，改完看不出效果。
    const params: { purpose?: string; agent_code?: string } =
      props.scopeType === 'purpose'
        ? { purpose: props.scopeKey }
        : props.scopeType === 'agent'
          ? { agent_code: props.scopeKey }
          : props.previewPurpose
            ? { purpose: props.previewPurpose }
            : {}
    try {
      resolution.value = await llmBindingApi.resolution(params)
    } catch {
      // 预览失败不打扰编辑：保存后仍可重看
      resolution.value = null
    }
  }

  function addRow(): void {
    entries.value.push({ supplier_id: '', model_name: '', priority: 0 })
  }

  function move(index: number, delta: number): void {
    const target = index + delta
    if (target < 0 || target >= entries.value.length) return
    const list = entries.value
    const [row] = list.splice(index, 1)
    list.splice(target, 0, row)
  }

  async function save(): Promise<void> {
    const filled = entries.value.filter(row => row.supplier_id)
    if (filled.length !== entries.value.length) {
      ElMessage.warning('每条兜底都要选供应商，或删除空行')
      return
    }
    if (new Set(filled.map(row => row.supplier_id)).size !== filled.length) {
      ElMessage.warning('同一层内供应商不能重复')
      return
    }
    saving.value = true
    try {
      // priority 由顺序推导：首档最高，保证「保存即可控顺序」与后端解析一致
      const total = filled.length
      await llmBindingApi.replace({
        scope_type: props.scopeType,
        scope_key: props.scopeKey,
        entries: filled.map((row, index) => ({
          supplier_id: row.supplier_id,
          model_name: (row.model_name || '').trim() || null,
          priority: (total - index) * 10,
          enabled: row.enabled ?? true,
        })),
      })
      ElMessage.success('模型绑定已保存并即时生效')
      emit('saved')
      await load()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  watch(
    () => [props.scopeType, props.scopeKey],
    () => void load(),
  )

  onMounted(() => {
    void load()
  })

  defineExpose({ load })
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
  .hint {
    margin-bottom: 12px;
  }
  .entry-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .entry-order {
    width: 64px;
    flex-shrink: 0;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .entry-supplier {
    width: 260px;
    flex-shrink: 0;
  }
  .entry-model {
    flex: 1;
    min-width: 0;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .resolution {
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid var(--ds-border);
  }
  .resolution-title {
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .resolution-hops {
    margin: 0;
    padding-left: 20px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .resolution-hops li {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
  }
  .resolution-name {
    font-weight: 600;
    color: var(--ds-text-1);
  }
</style>
