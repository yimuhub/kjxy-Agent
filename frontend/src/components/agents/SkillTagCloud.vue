<template>
  <div class="tag-cloud">
    <!-- 裁剪区：默认 3 行，超出隐藏（按钮必须在这一层之外，否则连按钮一起被裁） -->
    <div
      ref="itemsRef"
      class="tag-cloud-items"
      :class="{ 'is-clamped': !expanded }"
    >
      <template v-if="tags.length">
        <el-tag
          v-for="t in tags"
          :key="t"
          size="small"
          type="info"
        >
          {{ t }}
        </el-tag>
      </template>
      <span
        v-else
        class="tag-cloud-empty"
      >
        {{ emptyText }}
      </span>
    </div>
    <button
      v-if="overflow"
      type="button"
      class="tag-cloud-toggle"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? `收起 ▴` : `展开全部 ${tags.length} 个 ▾` }}
    </button>
  </div>
</template>

<script setup lang="ts">
  /**
   * 技能标签云（v3.65）：默认最多 3 行，超出自动折叠并显示
   * 「展开全部 N 个」切换按钮——卡片视图与列表视图共用，保证界面统一。
   * 行数折叠用 max-height（tag 行高固定），溢出检测用 ResizeObserver；
   * 切换按钮在裁剪区之外，保证折叠态也能看到它。
   */
  import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

  const props = withDefaults(
    defineProps<{
      tags: string[]
      emptyText?: string
    }>(),
    { emptyText: '未绑定' },
  )

  const itemsRef = ref<HTMLElement | null>(null)
  const expanded = ref(false)
  const overflow = ref(false)
  let observer: ResizeObserver | null = null

  function measure(): void {
    const el = itemsRef.value
    if (!el) return
    if (expanded.value) {
      // 展开态恒显示「收起」；收起后再测溢出
      overflow.value = true
      return
    }
    overflow.value = el.scrollHeight > el.clientHeight + 2
  }

  watch(
    () => props.tags,
    () => {
      expanded.value = false
      void nextTick(measure)
    },
    { deep: true },
  )

  onMounted(() => {
    void nextTick(measure)
    observer = new ResizeObserver(() => measure())
    if (itemsRef.value) observer.observe(itemsRef.value)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })
</script>

<style scoped>
  .tag-cloud {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .tag-cloud-items {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 5px;
    overflow: hidden;
  }
  .tag-cloud-items.is-clamped {
    /* 3 行：tag 高 24px（padding 6px×2 + 12px 字号）+ 行距 5px×2 */
    max-height: 86px;
  }
  .tag-cloud :deep(.el-tag) {
    margin-right: 0;
  }
  .tag-cloud-empty {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .tag-cloud-toggle {
    align-self: flex-end;
    padding: 0 4px;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 12px;
    line-height: 20px;
    color: var(--ds-accent-text, var(--el-color-primary));
  }
  .tag-cloud-toggle:hover {
    text-decoration: underline;
  }
</style>
