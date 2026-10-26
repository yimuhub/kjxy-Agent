<template>
  <el-popover
    :visible="open"
    placement="top-start"
    :width="popoverWidth"
    :show-arrow="false"
    :offset="8"
    popper-class="mention-popper"
    @update:visible="onVisible"
  >
    <template #reference>
      <slot name="reference" />
    </template>

    <div class="mention-panel">
      <p
        :id="titleId"
        class="panel-title"
      >
        点名本轮回答的对象
      </p>

      <ul
        :id="listboxId"
        role="listbox"
        class="panel-list"
        :aria-label="`${label}点名候选`"
        :aria-labelledby="titleId"
        tabindex="-1"
      >
        <li
          v-for="(c, i) in visibleItems"
          :id="`${uid}-opt-${i}`"
          :key="`${c.kind}-${c.id}`"
          role="option"
          class="panel-opt"
          :class="{ active: i === activeIndex }"
          :aria-selected="i === activeIndex"
          @mousedown.prevent
          @click="pick(c)"
          @mouseenter="activeIndex = i"
        >
          <span
            class="opt-emoji"
            aria-hidden="true"
          >{{ c.emoji || (c.kind === 'team' ? '👥' : '🤖') }}</span>
          <span class="opt-text">
            <span class="opt-name">{{ mentionDisplayName(c) }}</span>
            <span
              v-if="c.blurb"
              class="opt-blurb"
            >{{ c.blurb }}</span>
          </span>
          <span class="opt-kind">{{ c.kind === 'team' ? '班组' : '助手' }}</span>
        </li>
      </ul>

      <p
        v-if="loading"
        class="panel-tip"
      >
        候选加载中…
      </p>
      <p
        v-else-if="!visibleItems.length"
        class="panel-tip"
      >
        没有匹配的助手或班组
      </p>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
  import { computed, ref, watch } from 'vue'
  import { ElPopover } from 'element-plus'
  import { mentionDisplayName } from '@/api/chat'
  import type { MentionCandidate } from '@/api/chat'

  /**
   * @ 点名候选浮层：只做「列候选 + 前缀过滤 + 键盘选中」，不碰输入框内容。
   *
   * 焦点始终留在父级的 textarea（用户可继续打字过滤），所以 aria-activedescendant
   * 由父级挂在输入框上，本组件把当前高亮项的 DOM id 暴露出去。
   */
  const props = withDefaults(
    defineProps<{
      open: boolean
      candidates: MentionCandidate[]
      /** @ 之后已输入的片段，用于前缀过滤 */
      keyword?: string
      loading?: boolean
      /** 无障碍名称前缀（与 ChatPanel 的 label 保持一致） */
      label?: string
      popoverWidth?: number
    }>(),
    { keyword: '', loading: false, label: '对话', popoverWidth: 300 },
  )

  const emit = defineEmits<{
    (e: 'select', candidate: MentionCandidate): void
    (e: 'update:open', value: boolean): void
  }>()

  const uid = `mention-${Math.random().toString(36).slice(2, 8)}`
  const titleId = `${uid}-title`
  const listboxId = `${uid}-listbox`
  const activeIndex = ref(0)

  /** 前缀过滤：按展示名/原始名/编码比对，空关键词列全部 */
  const visibleItems = computed(() => {
    const kw = props.keyword.trim().toLowerCase()
    if (!kw) return props.candidates
    return props.candidates.filter(c => {
      const hay = [mentionDisplayName(c), c.name, c.code, c.blurb]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return hay.includes(kw)
    })
  })

  /** 高亮项 id：无候选时为 null，父级据此撤掉 aria-activedescendant */
  const activeOptionId = computed(() =>
    visibleItems.value.length ? `${uid}-opt-${Math.min(activeIndex.value, visibleItems.value.length - 1)}` : null,
  )

  watch(visibleItems, list => {
    if (activeIndex.value > list.length - 1) activeIndex.value = 0
  })

  watch(
    () => props.open,
    value => {
      if (value) activeIndex.value = 0
    },
  )

  function onVisible(value: boolean) {
    if (!value) emit('update:open', false)
  }

  function pick(candidate: MentionCandidate) {
    emit('select', candidate)
  }

  /**
   * 键盘导航：由父级 textarea 的 keydown 调用。
   * 返回 true 表示事件已被浮层消费（父级必须停止默认发送逻辑）。
   */
  function handleKeydown(event: KeyboardEvent): boolean {
    if (!props.open) return false
    const total = visibleItems.value.length
    if (event.key === 'Escape') {
      emit('update:open', false)
      return true
    }
    if (event.key === 'ArrowDown') {
      activeIndex.value = total ? (activeIndex.value + 1) % total : 0
      return true
    }
    if (event.key === 'ArrowUp') {
      activeIndex.value = total ? (activeIndex.value - 1 + total) % total : 0
      return true
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      if (!total) return false
      pick(visibleItems.value[Math.min(activeIndex.value, total - 1)])
      return true
    }
    return false
  }

  defineExpose({ handleKeydown, activeOptionId, listboxId })
</script>

<style scoped>
  .mention-panel {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .panel-title {
    margin: 0;
    padding: 0 2px 2px;
    font-size: 11.5px;
    letter-spacing: 0.02em;
    color: var(--ds-text-3);
  }

  .panel-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 232px;
    overflow-y: auto;
  }

  .panel-opt {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 7px 8px;
    border-radius: var(--ds-radius-control);
    border: 1px solid transparent;
    cursor: pointer;
  }
  .panel-opt.active {
    background: var(--ds-glass-strong);
    border-color: var(--ds-border);
  }

  .opt-emoji {
    flex: none;
    width: 20px;
    font-size: 15px;
    line-height: 1.4;
    text-align: center;
  }

  .opt-text {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .opt-name {
    font-size: 13px;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .opt-blurb {
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-text-3);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .opt-kind {
    flex: none;
    margin-left: auto;
    font-size: 11px;
    color: var(--ds-text-3);
    padding: 1px 6px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }

  .panel-tip {
    margin: 0;
    padding: 6px 2px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>

<style>
  /* 浮层被 teleport 到 body，scoped 样式管不到，这里统一弹层玻璃配方（§3） */
  .el-popover.el-popper.mention-popper {
    max-width: calc(100vw - 32px);
    padding: 8px;
    background: var(--ds-bg-2);
    border: 1px solid var(--ds-border-strong);
    border-radius: var(--ds-radius-card);
    box-shadow: var(--ds-shadow);
  }
</style>
