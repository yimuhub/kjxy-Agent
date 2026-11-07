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
            class="ext-row"
          >
            <span class="ext-row-head">
              <span class="ext-stale-name">{{ staleLabel(c) }}</span>
              <el-tag
                class="ext-tag"
                size="small"
                type="info"
                effect="plain"
              >
                已失效
              </el-tag>
            </span>
          </el-checkbox>
        </el-checkbox-group>

        <!-- 折叠态：最多显示前 3 行（页面统一化）；其余收进「展开全部」弹框 -->
        <el-checkbox-group
          v-if="collapsedRows.length"
          :model-value="modelValue"
          @update:model-value="onChange"
        >
          <el-checkbox
            v-for="o in collapsedRows"
            :key="o.code"
            :value="o.code"
            class="ext-row"
          >
            <span class="ext-row-head">
              <span
                class="ext-name"
                :title="o.name"
              >{{ o.name }}</span>
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
            </span>
            <span
              v-if="o.summary"
              class="ext-summary"
              :title="o.summary"
            >{{ o.summary }}</span>
          </el-checkbox>
        </el-checkbox-group>

        <!-- 展开全部：下拉弹框承载完整列表（含独立搜索），再点收回。
             挂载条件用全部可选项而非过滤结果：搜索无匹配时要留在弹层里显示空态，
             否则输入第一个字就会把弹层（连同搜索框）整个卸载掉。 -->
        <el-popover
          v-if="expanded || props.options.length > COLLAPSED_ROWS"
          :visible="expanded"
          placement="bottom-start"
          :width="popoverWidth"
          popper-class="ext-popover"
        >
          <template #reference>
            <button
              type="button"
              class="ext-toggle"
              :aria-expanded="expanded"
              @click="expanded = !expanded"
            >
              {{ expanded ? '收起全部 ▴' : `展开全部 ${props.options.length} 项 ▾` }}
            </button>
          </template>
          <div class="ext-popover-body">
            <el-input
              v-model="keyword"
              class="ext-search"
              size="small"
              clearable
              :placeholder="`按名称或摘要过滤${kindLabel}`"
            />
            <div class="ext-popover-list">
              <el-checkbox-group
                v-if="filteredOptions.length"
                :model-value="modelValue"
                @update:model-value="onChange"
              >
                <el-checkbox
                  v-for="o in filteredOptions"
                  :key="o.code"
                  :value="o.code"
                  class="ext-row"
                >
                  <span class="ext-row-head">
                    <span
                      class="ext-name"
                      :title="o.name"
                    >{{ o.name }}</span>
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
                  </span>
                  <span
                    v-if="o.summary"
                    class="ext-summary"
                    :title="o.summary"
                  >{{ o.summary }}</span>
                </el-checkbox>
              </el-checkbox-group>
              <div
                v-else
                class="ext-empty"
              >
                <span class="ext-empty-text">没有匹配「{{ keyword }}」的条目</span>
              </div>
            </div>
          </div>
        </el-popover>

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

  /** 折叠态默认显示的行数（v3.64：页面统一化，多于 3 行收进弹框） */
  const COLLAPSED_ROWS = 3

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

  /** 折叠态主列表：前 3 行（搜索命中同样只显示前 3 行，其余进弹框） */
  const collapsedRows = computed(() =>
    expanded.value ? [] : filteredOptions.value.slice(0, COLLAPSED_ROWS),
  )

  /** 展开态：主列表收起、由弹框承载全部；弹框宽度跟随容器（约 420px 起） */
  const expanded = ref(false)
  const popoverWidth = 440

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
    padding: 4px 8px;
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    background: var(--ds-bg-2);
  }
  /* 整行卡片式：覆盖 Element Plus 复选框默认的 inline-flex / nowrap / 固定 32px 高，
     否则多行摘要会与名称、标签叠字 */
  .ext-list :deep(.el-checkbox-group) {
    display: flex;
    flex-direction: column;
  }
  .ext-list :deep(.el-checkbox) {
    display: flex;
    align-items: flex-start;
    box-sizing: border-box;
    width: 100%;
    height: auto;
    margin: 0;
    padding: 7px 6px;
    white-space: normal;
    border-radius: 6px;
    transition: background-color 0.15s ease;
  }
  .ext-list :deep(.el-checkbox + .el-checkbox) {
    border-top: 1px solid var(--ds-border);
  }
  .ext-list :deep(.el-checkbox:hover) {
    background: color-mix(in srgb, var(--ds-accent) 8%, transparent);
  }
  .ext-list :deep(.el-checkbox__input) {
    flex: none;
    margin-top: 2px;
  }
  .ext-list :deep(.el-checkbox__label) {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: 100%;
    min-width: 0;
    padding-left: 8px;
    line-height: 1.5;
    white-space: normal;
  }
  .ext-stale-group {
    padding-bottom: 4px;
    margin-bottom: 4px;
    border-bottom: 1px dashed var(--ds-border);
  }
  .ext-row-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    min-width: 0;
  }
  .ext-stale-name {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    line-height: 1.5;
    color: var(--ds-text-3);
    overflow-wrap: anywhere;
  }
  .ext-name {
    max-width: 100%;
    overflow: hidden;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.5;
    color: var(--ds-text-1);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ext-tag {
    flex: none;
    margin-left: 0;
  }
  .ext-summary {
    display: block;
    font-size: 12px;
    line-height: 1.5;
    color: var(--ds-text-3);
    overflow-wrap: anywhere;
  }
  /* 折叠切换按钮：贴列表底部，整行宽度 */
  .ext-toggle {
    display: block;
    width: 100%;
    margin-top: 4px;
    padding: 6px 4px;
    border: none;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;
    font-size: 12.5px;
    color: var(--ds-accent-text, var(--el-color-primary));
    text-align: center;
    transition: background-color 0.15s ease;
  }
  .ext-toggle:hover {
    background: color-mix(in srgb, var(--ds-accent) 10%, transparent);
  }
  /* 弹框：完整列表，高度受限内滚 */
  .ext-popover-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .ext-popover-list {
    max-height: 320px;
    overflow-y: auto;
  }
  .ext-popover-list :deep(.el-checkbox-group) {
    display: flex;
    flex-direction: column;
  }
  .ext-popover-list :deep(.el-checkbox) {
    display: flex;
    align-items: flex-start;
    box-sizing: border-box;
    width: 100%;
    height: auto;
    margin: 0;
    padding: 7px 6px;
    white-space: normal;
    border-radius: 6px;
  }
  .ext-popover-list :deep(.el-checkbox + .el-checkbox) {
    border-top: 1px solid var(--ds-border);
  }
  .ext-popover-list :deep(.el-checkbox:hover) {
    background: color-mix(in srgb, var(--ds-accent) 8%, transparent);
  }
  .ext-popover-list :deep(.el-checkbox__input) {
    flex: none;
    margin-top: 2px;
  }
  .ext-popover-list :deep(.el-checkbox__label) {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: 100%;
    min-width: 0;
    padding-left: 8px;
    line-height: 1.5;
    white-space: normal;
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
