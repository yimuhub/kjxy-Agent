<template>
  <div class="ext-picker">
    <!-- 加载失败：不整页报错，已有绑定值原样保留，可重试 -->
    <div
      v-if="error"
      class="ext-error"
    >
      <span class="ext-error-text">
        可选{{ kindLabel }}加载失败，已绑定的 {{ selectedCount }} 项会原样保留
      </span>
      <el-button
        size="small"
        type="primary"
        plain
        :loading="loading"
        @click="emit('retry')"
      >
        加载失败，重试
      </el-button>
    </div>
    <template v-else>
      <div class="ext-toolbar">
        <el-input
          v-model="keyword"
          class="ext-search"
          size="small"
          clearable
          :placeholder="`按名称或摘要过滤${kindLabel}`"
          :aria-label="`过滤可选${kindLabel}`"
        />
        <span class="ext-count">已选 {{ selectedCount }}</span>
        <el-button
          size="small"
          link
          type="primary"
          :disabled="!filteredOptions.length"
          @click="selectAllFiltered"
        >
          全选
        </el-button>
        <el-button
          size="small"
          link
          :disabled="!clearableCount"
          @click="clearKind"
        >
          清空
        </el-button>
      </div>

      <div class="ext-list">
        <!-- 已下架/无权限的历史绑定：禁用展示，标签「已失效」，避免静默丢数据 -->
        <el-checkbox-group
          v-if="staleCodes.length"
          class="ext-stale-group"
          :model-value="staleCodes"
          disabled
        >
          <el-checkbox
            v-for="c in staleCodes"
            :key="c"
            :value="c"
          >
            <span class="ext-stale-name">{{ staleLabel(c) }}</span>
            <el-tag
              class="ext-tag"
              size="small"
              type="info"
              effect="plain"
            >
              已失效
            </el-tag>
          </el-checkbox>
        </el-checkbox-group>

        <el-checkbox-group
          v-if="filteredOptions.length"
          :model-value="modelValue"
          @update:model-value="onChange"
        >
          <el-checkbox
            v-for="o in filteredOptions"
            :key="o.code"
            :value="o.code"
          >
            <span class="ext-name">{{ o.name }}</span>
            <el-tag
              class="ext-tag"
              size="small"
              effect="plain"
            >
              {{ sourceLabel(o.source) }}
            </el-tag>
            <el-tag
              class="ext-tag"
              size="small"
              :type="RISK_TAG_TYPES[o.risk] ?? 'info'"
              effect="plain"
            >
              {{ RISK_LABELS[o.risk] ?? o.risk }}
            </el-tag>
            <span
              v-if="o.summary"
              class="ext-summary"
            >{{ o.summary }}</span>
          </el-checkbox>
        </el-checkbox-group>

        <div
          v-if="!filteredOptions.length && !staleCodes.length"
          class="ext-empty"
        >
          <span class="ext-empty-text">
            {{ loading ? `正在加载可选${kindLabel}…` : emptyText }}
          </span>
          <el-button
            v-if="!loading"
            size="small"
            link
            type="primary"
            @click="goLibrary"
          >
            去插件与技能库
          </el-button>
        </div>
        <div
          v-else-if="!filteredOptions.length && staleCodes.length"
          class="ext-empty"
        >
          <span class="ext-empty-text">当前没有可选{{ kindLabel }}，已绑定的条目仍会保留</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import type { CheckboxValueType } from 'element-plus'

  /** 单个可绑定条目（code 已含 skill:/plugin: 前缀，即 bound_skills 的存储值） */
  interface BindableExtensionOption {
    code: string
    name: string
    summary: string
    source: string
    risk: 'low' | 'medium' | 'high'
  }

  const props = withDefaults(
    defineProps<{
      /** 绑定值数组（与另一区块共享同一份 bound_skills） */
      modelValue: string[]
      kind: 'skill' | 'plugin'
      options: BindableExtensionOption[]
      /** 已绑定但不在可选集合中的 code（已下架/无权限），禁用展示 */
      staleCodes?: string[]
      error?: boolean
      loading?: boolean
      emptyText?: string
    }>(),
    {
      staleCodes: () => [],
      error: false,
      loading: false,
      emptyText: '暂无可绑定条目',
    },
  )

  const emit = defineEmits<{
    (e: 'update:modelValue', value: string[]): void
    (e: 'retry'): void
  }>()

  const router = useRouter()
  const keyword = ref('')

  const kindLabel = computed(() => (props.kind === 'skill' ? '扩展技能' : '扩展插件'))
  const kindPrefix = computed(() => (props.kind === 'skill' ? 'skill:' : 'plugin:'))

  const SOURCE_LABELS: Record<string, string> = {
    builtin: '内置',
    market: '市场',
    local: '本地',
    imported: '导入',
    generated: '生成',
  }
  const RISK_LABELS: Record<string, string> = { low: '低', medium: '中', high: '高' }
  const RISK_TAG_TYPES: Record<string, 'success' | 'warning' | 'danger'> = {
    low: 'success',
    medium: 'warning',
    high: 'danger',
  }

  function sourceLabel(source: string): string {
    return SOURCE_LABELS[source] ?? source
  }

  function staleLabel(code: string): string {
    return code.replace(/^(skill|plugin):/, '') || code
  }

  const filteredOptions = computed(() => {
    const kw = keyword.value.trim().toLowerCase()
    if (!kw) return props.options
    return props.options.filter(
      o => o.name.toLowerCase().includes(kw) || o.summary.toLowerCase().includes(kw),
    )
  })

  /** 已选数量：该区块前缀下的全部绑定值（含失效项） */
  const selectedCount = computed(
    () => props.modelValue.filter(c => c.startsWith(kindPrefix.value)).length,
  )

  /** 可被「清空」移除的数量：仅统计当前可选集合中的勾选项，失效项不可移除 */
  const clearableCount = computed(() => {
    const codes = new Set(props.options.map(o => o.code))
    return props.modelValue.filter(c => codes.has(c)).length
  })

  function onChange(values: CheckboxValueType[]) {
    emit(
      'update:modelValue',
      values.map(v => String(v)),
    )
  }

  function selectAllFiltered() {
    const next = new Set(props.modelValue)
    for (const o of filteredOptions.value) next.add(o.code)
    emit('update:modelValue', Array.from(next))
  }

  function clearKind() {
    const removable = new Set(props.options.map(o => o.code))
    emit(
      'update:modelValue',
      props.modelValue.filter(c => !removable.has(c)),
    )
  }

  function goLibrary() {
    void router.push('/library')
  }
</script>

<style scoped>
  .ext-picker {
    width: 100%;
  }
  .ext-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 6px;
  }
  .ext-search {
    width: 200px;
  }
  .ext-count {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .ext-list {
    max-height: 180px;
    overflow-y: auto;
    padding: 6px 12px;
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    background: var(--ds-bg-2);
  }
  .ext-stale-group {
    display: block;
    padding-bottom: 4px;
    margin-bottom: 4px;
    border-bottom: 1px dashed var(--ds-border);
  }
  .ext-stale-name {
    font-family: var(--ds-font-mono);
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .ext-name {
    font-weight: 500;
  }
  .ext-tag {
    margin-left: 4px;
  }
  .ext-summary {
    display: block;
    margin-top: 2px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .ext-empty {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
  }
  .ext-empty-text {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .ext-error {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    padding: 8px 12px;
    border: 1px solid color-mix(in srgb, var(--ds-danger) 35%, transparent);
    border-radius: 8px;
    background: var(--ds-bg-2);
  }
  .ext-error-text {
    font-size: 12px;
    color: var(--ds-text-2);
  }
</style>
