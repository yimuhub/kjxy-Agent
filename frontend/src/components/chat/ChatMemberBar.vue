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
    </header>

    <el-dialog
      v-model="addOpen"
      :title="`添加${label}成员`"
      width="520px"
      append-to-body
      class="add-member-dialog"
      @opened="focusSearch"
    >
      <el-input
        ref="searchInput"
        v-model="search"
        class="search-input"
        placeholder="搜索助手或班组：名称 / 编码 / 简介"
        clearable
        :prefix-icon="Search"
        aria-label="搜索可添加的助手或班组"
      />
      <div
        class="add-body"
        role="listbox"
        :aria-label="`可添加的${label}成员`"
      >
        <section
          v-for="group in groupedCandidates"
          :key="group.kind"
          class="cand-group"
        >
          <h4 class="group-title">
            {{ group.title }}
            <span class="group-count">{{ group.items.length }}</span>
          </h4>
          <ul class="cand-list">
            <li
              v-for="c in group.items"
              :key="`${c.kind}-${c.id}`"
              class="cand-item"
              :class="{ joined: c.joined }"
              role="option"
              :aria-selected="c.joined"
            >
              <span
                class="cand-avatar"
                aria-hidden="true"
              >{{ c.emoji || (c.kind === 'team' ? '👥' : '🤖') }}</span>
              <span class="cand-text">
                <span class="cand-name">{{ displayName(c) }}</span>
                <span
                  v-if="c.blurb"
                  class="cand-blurb"
                >{{ c.blurb }}</span>
              </span>
              <el-button
                size="small"
                :type="c.joined ? 'info' : 'primary'"
                :plain="!c.joined"
                :disabled="c.joined || full"
                :loading="busy && pendingId === c.id"
                @click="onAdd(c)"
              >
                {{ c.joined ? '已在群聊' : '添加' }}
              </el-button>
            </li>
          </ul>
        </section>
        <p
          v-if="store.mentionLoading"
          class="cand-tip"
        >
          候选加载中…
        </p>
        <el-empty
          v-else-if="!filteredCandidates.length"
          :image-size="64"
          :description="search ? '没有匹配的助手，换个关键词试试' : '没有可添加的对象'"
        />
      </div>
      <template #footer>
        <span class="footer-tip">已加入 {{ store.participants.length }}/{{ maxMembers }}；可连续添加多位</span>
        <el-button @click="addOpen = false">
          完成
        </el-button>
      </template>
    </el-dialog>

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
  import { Bottom, Close, Plus, Search, Top, Warning } from '@element-plus/icons-vue'
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
  const pendingId = ref('')
  const search = ref('')
  const searchInput = ref<{ focus: () => void } | null>(null)

  /** 成员行：store 已按 position 排好并补齐「未回答」说明 */
  const rows = computed(() => props.store.memberRounds)
  const full = computed(() => props.store.participants.length >= maxMembers)
  const missingCount = computed(() => rows.value.filter(m => m.missing).length)

  /** 候选行：带上「是否已在群聊」，让列表稳定、不因加入而跳动 */
  const candidateRows = computed(() => {
    const inGroup = new Set<string>()
    for (const p of props.store.participants) {
      if (p.agent_id) inGroup.add(p.agent_id)
      if (p.agent_code) inGroup.add(p.agent_code)
    }
    return props.store.mentionCandidates.map(c => ({
      ...c,
      joined: inGroup.has(c.id) || Boolean(c.code && inGroup.has(c.code)),
    }))
  })

  const filteredCandidates = computed(() => {
    const q = search.value.trim().toLowerCase()
    if (!q) return candidateRows.value
    return candidateRows.value.filter(c =>
      [displayName(c), c.code, c.blurb]
        .filter(Boolean)
        .some(v => String(v).toLowerCase().includes(q)),
    )
  })

  const groupedCandidates = computed(() =>
    [
      { kind: 'agent', title: '助手', items: filteredCandidates.value.filter(c => c.kind !== 'team') },
      { kind: 'team', title: '班组', items: filteredCandidates.value.filter(c => c.kind === 'team') },
    ].filter(g => g.items.length > 0),
  )

  /** 后端可能多回一个成员表外的 role：它只读，不给调序/删除按钮 */
  function isLastVisible(index: number): boolean {
    return index >= rows.value.length - 1
  }

  async function onToggleAdd() {
    if (full.value) return
    addOpen.value = !addOpen.value
    if (addOpen.value) {
      search.value = ''
      await props.store.loadMentionCandidates()
    }
  }

  function focusSearch() {
    searchInput.value?.focus()
  }

  /** 添加后不关闭弹窗：允许连续把多位助手/班组拉进来 */
  async function onAdd(candidate: MentionCandidate & { joined?: boolean }) {
    if (candidate.joined || full.value) return
    pendingId.value = candidate.id
    busy.value = true
    await props.store.addParticipant(candidate)
    busy.value = false
    pendingId.value = ''
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
  /* 成员条：融进会话信息行的一行灰字，不再自带卡片外框 */
  .member-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 10px;
    min-width: 0;
    padding: 0;
    background: transparent;
    border: 0;
  }

  .bar-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
  }

  .bar-title {
    font-size: 12px;
    color: var(--ds-text-3);
    white-space: nowrap;
  }

  .add-btn {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    height: 24px;
    padding: 0 8px;
    font-size: 11.5px;
    color: var(--ds-text-3);
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    cursor: pointer;
    transition:
      background 0.15s,
      color 0.15s;
  }
  .add-btn:hover:not(:disabled) {
    color: var(--ds-text-1);
    background: var(--ds-glass-strong);
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
    gap: 5px;
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
    flex: 1 1 100%;
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

  .search-input {
    margin-bottom: 10px;
  }

  .add-body {
    max-height: min(52vh, 420px);
    overflow-y: auto;
    padding-right: 2px;
  }

  .cand-group + .cand-group {
    margin-top: 10px;
  }

  .group-title {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 6px;
    font-size: 12px;
    font-weight: 600;
    color: var(--ds-text-3);
  }
  .group-count {
    font-weight: 400;
    color: var(--ds-text-3);
  }

  .cand-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .cand-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 8px;
    border: 1px solid transparent;
    border-radius: 10px;
  }
  .cand-item:hover {
    background: var(--ds-glass-strong);
    border-color: var(--ds-border);
  }
  .cand-item.joined {
    opacity: 0.62;
  }

  .cand-avatar {
    flex: none;
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    border-radius: 50%;
  }

  .cand-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .cand-name {
    font-size: 13px;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cand-blurb {
    font-size: 11.5px;
    line-height: 1.35;
    color: var(--ds-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cand-tip {
    margin: 10px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .footer-tip {
    margin-right: auto;
    font-size: 12px;
    color: var(--ds-text-3);
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
