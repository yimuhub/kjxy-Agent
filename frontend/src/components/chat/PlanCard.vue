<template>
  <div
    class="plan-card"
    :class="card.complete ? 'is-complete' : 'is-incomplete'"
  >
    <div class="head">
      <span class="kind">多源方案</span>
      <el-tag
        size="small"
        :type="card.complete ? 'success' : 'warning'"
        effect="plain"
      >
        {{ card.complete ? '章节齐全' : `待补 ${gaps.length} 处` }}
      </el-tag>
      <span class="counts">{{ counts }}</span>
    </div>

    <ul
      v-if="gaps.length"
      class="gap-list"
    >
      <li
        v-for="gap in gaps"
        :key="gap"
      >
        {{ gap }}：资料不足或写得过浅，投产前请转专家复核
      </li>
    </ul>
    <ul
      v-if="card.warnings?.length"
      class="warn-list"
    >
      <li
        v-for="(warn, i) in card.warnings"
        :key="i"
      >
        {{ warn }}
      </li>
    </ul>

    <div class="actions">
      <el-button
        size="small"
        :loading="exporting"
        @click="emit('export')"
      >
        导出为文档
      </el-button>
      <el-button
        size="small"
        @click="emit('escalate')"
      >
        转班组会诊
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import type { ChatPlanCard } from '@/api/chat'

  /**
   * 方案卡片（v3.14）：正文就是上面那条 Markdown 消息，卡片只讲三件事——
   * 齐不齐（缺哪些章节）、依据来自哪（知识库/联网/技能），以及下一步做什么。
   */
  const props = defineProps<{
    card: ChatPlanCard
    exporting?: boolean
  }>()

  const emit = defineEmits<{
    (e: 'export'): void
    (e: 'escalate'): void
  }>()

  const gaps = computed(() => props.card.missing_sections || [])

  const counts = computed(() => {
    const ev = props.card.evidence || {}
    const parts = [`知识库 ${ev.knowledge || 0} 条`]
    if (ev.web_skipped === 'knowledge_enough') parts.push('证据充足未联网')
    else if (ev.web_skipped === 'disabled') parts.push('本次未联网')
    else parts.push(`联网 ${ev.web || 0} 条`)
    if (ev.skills?.length) parts.push(`技能 ${ev.skills.length} 项`)
    return parts.join(' · ')
  })
</script>

<style scoped>
  .plan-card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-md);
    background: var(--ds-glass);
  }

  .plan-card.is-incomplete {
    border-color: var(--ds-warning, #e6a23c);
  }

  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .kind {
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .counts {
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .gap-list,
  .warn-list {
    margin: 0;
    padding-left: 18px;
    font-size: 13px;
    color: var(--ds-text-2);
  }

  .warn-list {
    color: var(--ds-text-3);
  }

  .actions {
    display: flex;
    gap: 8px;
  }
</style>
