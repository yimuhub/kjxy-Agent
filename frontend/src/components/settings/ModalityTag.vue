<template>
  <el-tag
    :type="meta.type"
    :size="size"
    effect="plain"
    class="modality-tag"
    :aria-label="`模态：${meta.label}`"
  >
    {{ meta.label }}
  </el-tag>
</template>

<script setup lang="ts">
  /** 模型模态彩色标签：文本/图像/视频/多模态/嵌入/重排/ASR/TTS。 */
  import { computed } from 'vue'
  import { modalityMeta } from '@/api/modelChannels'

  const props = withDefaults(
    defineProps<{
      modality?: string | null
      size?: 'small' | 'default' | 'large'
    }>(),
    { modality: '', size: 'small' },
  )

  const meta = computed(() => modalityMeta(props.modality))
</script>

<style scoped>
  .modality-tag {
    margin-right: 4px;
  }
</style>
