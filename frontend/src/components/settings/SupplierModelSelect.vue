<template>
  <el-select
    :model-value="currentValue"
    filterable
    :loading="loading"
    :disabled="disabled"
    :placeholder="placeholder"
    class="supplier-model-select"
    style="width: 100%"
    @update:model-value="onPick"
  >
    <el-option-group
      v-for="group in groups"
      :key="group.supplier.id"
      :label="group.supplier.name"
    >
      <el-option
        v-if="allowDefault"
        :label="`${group.supplier.name} · 使用默认模型`"
        :value="`default::${group.supplier.id}`"
      />
      <el-option
        v-for="option in group.options"
        :key="option.value"
        :label="option.label"
        :value="option.value"
      />
    </el-option-group>
    <el-option
      v-if="orphanLabel"
      :label="orphanLabel"
      :value="currentValue"
    />
  </el-select>
</template>

<script setup lang="ts">
  /** 供应商 → 模型 级联下拉（v3.37）：一个控件里按供应商分组列模型，值=模型行 id。
   *
   * 只列「已启用的供应商 + 已启用且模态匹配的模型」，避免把对话模型绑进图像/视频能力。
   */
  import { computed, ref, watch } from 'vue'
  import { modalityMeta } from '@/api/modelChannels'
  import type { LlmSupplier } from '@/api/modules'
  import {
    loadModelOptions,
    modelLabel,
    type ModelChoice,
    type ModelOption,
  } from '@/composables/useSupplierModels'

  const props = withDefaults(
    defineProps<{
      supplierId?: string | null
      modelId?: string | null
      suppliers: LlmSupplier[]
      /** 允许的模态；空数组=不过滤 */
      modalities?: string[]
      allowDefault?: boolean
      disabled?: boolean
      placeholder?: string
      /** 当前模型不在候选里时（已停用/已删除）仍显示的可读文案 */
      fallbackLabel?: string
      /** id=值取模型行 id（绑定用）；name=值取上游模型名（助手编辑页旧设置用） */
      valueMode?: 'id' | 'name'
    }>(),
    {
      supplierId: '',
      modelId: '',
      modalities: () => [],
      allowDefault: false,
      disabled: false,
      placeholder: '选择供应商 · 模型',
      fallbackLabel: '',
      valueMode: 'id',
    },
  )

  const emit = defineEmits<{
    (event: 'update:supplierId', value: string): void
    (event: 'update:modelId', value: string): void
    (event: 'change', value: ModelChoice): void
  }>()

  const options = ref<ModelOption[]>([])
  const loading = ref(false)

  const DEFAULT_PREFIX = 'default::'

  const currentValue = computed(() => {
    if (props.modelId) return props.modelId
    if (props.supplierId && props.allowDefault) return `${DEFAULT_PREFIX}${props.supplierId}`
    return ''
  })

  const groups = computed(() =>
    props.suppliers
      .filter(supplier => supplier.status === 1)
      .map(supplier => ({
        supplier,
        options: options.value
          .filter(item => item.supplier.id === supplier.id)
          .map(item => ({
            value: optionValue(item.model),
            // 收起后只显示选中项：带上供应商名，跨供应商同名模型也能一眼分清
            label: `${supplier.name} · ${modelLabel(item.model)} · ${
              modalityMeta(item.model.modality).label
            }`,
          })),
      }))
      // 不限制模态时可以只绑供应商（用它自己的默认模型），没有模型行也要能选
      .filter(group => group.options.length || props.allowDefault),
  )

  function optionValue(model: {
    id?: string | null
    upstream_model_id?: string | null
    model_key?: string | null
  }): string {
    if (props.valueMode === 'name') {
      return model.upstream_model_id || model.model_key || String(model.id ?? '')
    }
    return String(model.id ?? '')
  }

  const orphanLabel = computed(() => {
    const value = currentValue.value
    if (!value) return ''
    const known = groups.value.some(
      group =>
        group.options.some(option => option.value === value) ||
        (props.allowDefault && value === `${DEFAULT_PREFIX}${group.supplier.id}`),
    )
    if (known) return ''
    return props.fallbackLabel || '当前模型（不在候选列表，可能已停用）'
  })

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      options.value = await loadModelOptions(props.suppliers, props.modalities)
    } finally {
      loading.value = false
    }
  }

  function onPick(value: string): void {
    if (value.startsWith(DEFAULT_PREFIX)) {
      const supplierId = value.slice(DEFAULT_PREFIX.length)
      const supplier = props.suppliers.find(item => item.id === supplierId)
      if (!supplier) return
      emit('update:supplierId', supplier.id)
      emit('update:modelId', '')
      emit('change', {
        supplier_id: supplier.id,
        supplier_model_id: '',
        model_name: '',
        supplier_name: supplier.name,
      })
      return
    }
    const hit = options.value.find(item => optionValue(item.model) === value)
    if (!hit) return
    emit('update:supplierId', hit.supplier.id)
    emit('update:modelId', value)
    emit('change', {
      supplier_id: hit.supplier.id,
      supplier_model_id: props.valueMode === 'id' ? String(hit.model.id) : '',
      model_name: hit.model.upstream_model_id || hit.model.model_key || '',
      supplier_name: hit.supplier.name,
    })
  }

  watch(
    () => [props.suppliers, props.modalities],
    () => void refresh(),
    { immediate: true, deep: false },
  )

  defineExpose({ refresh, options })
</script>

<style scoped>
  .supplier-model-select {
    min-width: 0;
  }
</style>
