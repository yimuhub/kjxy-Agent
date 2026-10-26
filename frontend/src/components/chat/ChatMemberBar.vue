<template>
  <section
    class="member-bar"
    :aria-label="`${label}群聊成员`"
  >
    <header class="bar-head">
      <span
        :id="titleId"
        class="bar-title"
      >群聊成员 {{ store.participants.length }}/{{ maxMembers }}</span>
      <el-popover
        :visible="addOpen"
        placement="bottom-end"
        :width="300"
        :show-arrow="false"
        popper-class="mention-popper add-popper"
        @update:visible="onVisible"
      >
        <template #reference>
          <button
            class="add-btn"
            type="button"
            :disabled="disabled || full"
            :title="full ? `最多 ${maxMembers} 位成员` : '把助手或班组拉进群聊'"
            :aria-label="`向${label}添加群聊成员`"
            @click="onToggleAdd"
          >
            <el-icon><Plus /></el-icon>
            添加
          </button>
        </template>
        <div class="add-panel">
          <p
            :id="`${titleId}-add`"
            class="add-title"
          >
            选择要拉进来的助手或班组
          </p>
          <ul
            role="listbox"
            class="add-list"
            :aria-labelledby="`${titleId}-add`"
          >
            <li
              v-for="c in addableCandidates"
              :key="`${c.kind}-${c.id}`"
              role="option"
              :aria-selected="false"
              class="add-opt"
              @mousedown.prevent
              @click="onAdd(c)"
            >
              <span
                class="opt-emoji"
                aria-hidden="true"
              >{{ c.emoji || (c.kind === 'team' ? '👥' : '🤖') }}</span>
              <span class="opt-text">
                <span class="opt-name">{{ displayName(c) }}</span>
                <span
                  v-if="c.blurb"
                  class="opt-blurb"
                >{{ c.blurb }}</span>
              </span>
            </li>
          </ul>
          <p
            v-if="store.mentionLoading"
            class="add-tip"
          >
            候选加载中…
          </p>
          <p
            v-else-if="!addableCandidates.length"
            class="add-tip"
          >
            没有可添加的对象（候选接口未就绪或都已加入）
          </p>
        </div>
      </el-popover>
    </header>

    <ul
      v-if="rows.length"
      class="member-list"
      :aria-label="`${label}成员本轮应答状态`"
    >
      <li
        v-for="(m, i) in rows"
        :key="m.key || m.name"
        class="member-chip"
        :class="{ missing: m.missing, extra: m.extra }"
      >
        <span
          class="avatar"
          aria-hidden="true"
        >{{ m.emoji || m.name.slice(0, 1) }}</span>
        <span class="m-text">
          <span class="m-name">{{ m.name }}</span>
          <el-tooltip
            :content="m.note"
            placement="top"
          >
            <span
              class="m-note"
              :class="m.status"
            >{{ m.note }}</span>
          </el-tooltip>
        </span>
        <span
          v-if="!m.extra"
          class="m-ops"
        >
          <button
            class="op"
            type="button"
            :disabled="disabled || i === 0"
            :aria-label="`把 ${m.name} 前移一位`"
            @click="onMove(m.key, -1)"
          >
            <el-icon><Top /></el-icon>
          </button>
          <button
            class="op"
            type="button"
            :disabled="disabled || i === rows.length - 1 || isLastVisible(i)"
            :aria-label="`把 ${m.name} 后移一位`"
            @click="onMove(m.key, 1)"
          >
            <el-icon><Bottom /></el-icon>
          </button>
          <button
            class="op del"
            type="button"
            :disabled="disabled || busy"
            :aria-label="`把 ${m.name} 移出群聊`"
            @click="onRemove(m.key)"
          >
            <el-icon><Close /></el-icon>
          </button>
        </span>
      </li>
    </ul>

    <p
      v-else
      class="bar-empty"
    >
      还没有群聊成员：点「添加」把助手或班组拉进来，就能一轮问多人。
    </p>

    <p
      v-if="missingCount"
      class="bar-alert"
      role="alert"
    >
      <el-icon><Warning /></el-icon>
      本轮有 {{ missingCount }} 位成员未回答，原因见各自状态标注。
    </p>
  </section>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElPopover } from 'element-plus'
  import { Bottom, Close, Plus, Top, Warning } from '@element-plus/icons-vue'
  import { MAX_GROUP_MEMBERS, mentionDisplayName } from '@/api/chat'
  import type { MentionCandidate } from '@/api/chat'
  import type { ChatStoreInstance } from '@/stores/chat'

  /**
   * 群聊成员条：固定成员增删 + 调序 + 本轮 member_event 实况。
   * 状态全部来自 store prop（与 ChatPanel 同一实例），组件本身不持对话状态。
   */
  const props = withDefaults(
    defineProps<{
      store: ChatStoreInstance
      /** 发送中/外部禁用时锁住增删与调序，避免一轮跑到一半改成员 */
      disabled?: boolean
      label?: string
    }>(),
    { disabled: false, label: '对话' },
  )

  const maxMembers = MAX_GROUP_MEMBERS
  const titleId = `member-title-${Math.random().toString(36).slice(2, 8)}`
  const addOpen = ref(false)
  const busy = ref(false)

  /** 成员行：store 已按 position 排好并补齐「未回答」说明 */
  const rows = computed(() => props.store.memberRounds)
  const full = computed(() => props.store.participants.length >= maxMembers)
  const missingCount = computed(() => rows.value.filter(m => m.missing).length)

  const addableCandidates = computed(() => {
    const inGroup = new Set(props.store.participants.map(p => p.agent_id || p.agent_code || ''))
    return props.store.mentionCandidates.filter(c => !inGroup.has(c.id) && !inGroup.has(c.code ?? ''))
  })

  /** 后端可能多回一个成员表外的 role：它只读，不给调序/删除按钮 */
  function isLastVisible(index: number): boolean {
    return index >= rows.value.length - 1
  }

  /** 弹层可见性由本组件的按钮控制：只接受外部的「关闭」，避免点参考区反复开关 */
  function onVisible(value: boolean) {
    if (!value) addOpen.value = false
  }

  async function onToggleAdd() {
    addOpen.value = !addOpen.value
    if (addOpen.value) await props.store.loadMentionCandidates()
  }

  async function onAdd(candidate: MentionCandidate) {
    busy.value = true
    const ok = await props.store.addParticipant(candidate)
    busy.value = false
    if (ok) addOpen.value = false
  }

  async function onRemove(key: string) {
    busy.value = true
    await props.store.removeParticipant(key)
    busy.value = false
  }

  async function onMove(key: string, delta: number) {
    busy.value = true
    await props.store.moveParticipant(key, delta)
    busy.value = false
  }

  function displayName(c: MentionCandidate) {
    return mentionDisplayName(c)
  }
</script>

<style scoped>
  .member-bar {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 10px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }

  .bar-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  .bar-title {
    font-size: 12.5px;
    color: var(--ds-text-2);
  }

  .add-btn {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    padding: 2px 9px;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
  }
  .add-btn:hover:not(:disabled) {
    color: var(--ds-accent-text);
    border-color: var(--ds-border-strong);
  }
  .add-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .member-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .member-chip {
    display: flex;
    align-items: center;
    gap: 7px;
    max-width: 100%;
    min-width: 0;
    padding: 4px 6px 4px 4px;
    background: var(--ds-bg-1);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }
  .member-chip.missing {
    border-color: color-mix(in srgb, var(--ds-warning) 45%, transparent);
  }
  .member-chip.extra {
    border-style: dashed;
  }

  .avatar {
    flex: none;
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    color: var(--ds-text-1);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }

  .m-text {
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .m-name {
    max-width: 148px;
    font-size: 12.5px;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .m-note {
    max-width: 172px;
    font-size: 11px;
    line-height: 1.4;
    color: var(--ds-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .m-note.running {
    color: var(--ds-accent-text);
  }
  .m-note.done {
    color: var(--ds-success);
  }
  .m-note.error,
  .m-note.unknown {
    color: var(--ds-warning);
  }

  .m-ops {
    flex: none;
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .op {
    width: 18px;
    height: 18px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: var(--ds-text-3);
    background: transparent;
    border: none;
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
  }
  .op:hover:not(:disabled) {
    color: var(--ds-text-1);
    background: var(--ds-glass-strong);
  }
  .op:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }
  .op.del:hover:not(:disabled) {
    color: var(--ds-danger);
  }

  .bar-empty {
    margin: 0;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-text-3);
  }

  .bar-alert {
    display: flex;
    align-items: center;
    gap: 5px;
    margin: 0;
    padding: 5px 8px;
    font-size: 11.5px;
    color: var(--ds-warning);
    background: color-mix(in srgb, var(--ds-warning) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--ds-warning) 32%, transparent);
    border-radius: var(--ds-radius-control);
  }

  .add-panel {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .add-title,
  .add-tip {
    margin: 0;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .add-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 220px;
    overflow-y: auto;
  }
  .add-opt {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--ds-radius-control);
    cursor: pointer;
  }
  .add-opt:hover {
    background: var(--ds-glass-strong);
  }
  .opt-emoji {
    flex: none;
    width: 20px;
    font-size: 15px;
    text-align: center;
  }
  .opt-text {
    min-width: 0;
    display: flex;
    flex-direction: column;
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
    color: var(--ds-text-3);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  @media (max-width: 640px) {
    .m-name {
      max-width: 96px;
    }
    .m-note {
      max-width: 118px;
    }
  }
</style>
