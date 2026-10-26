<template>
  <div v-if="card" class="card" :class="card.type">
    <div class="head">
      <span class="kind">{{ card.type === 'capability' ? '能力卡片' : '能力市场' }}</span>
      <el-tag
        size="small"
        :type="stateTagType"
        effect="plain"
      >
        {{ stateLabel }}
      </el-tag>
    </div>

    <div class="title">
      {{ card.name }}
    </div>
    <p
      v-if="card.description"
      class="desc"
    >
      {{ card.description }}
    </p>

    <!-- 能力卡片：编码 / 风险 / 状态 / 工具白名单 -->
    <template v-if="card.type === 'capability'">
      <div class="meta">
        <span
          v-if="card.code"
          class="kv"
        >
          <i>编码</i>{{ card.code }}
        </span>
        <span
          v-if="card.risk_level"
          class="kv"
        >
          <i>风险</i>
          <em :class="card.risk_level">{{ card.risk_level === 'low' ? '低' : '高' }}</em>
        </span>
        <span
          v-if="card.status"
          class="kv"
        >
          <i>状态</i>{{ statusLabel(card.status) }}
        </span>
      </div>
      <div
        v-if="card.tools?.length"
        class="tools"
      >
        <span
          v-for="t in card.tools"
          :key="t"
          class="tool"
        >{{ t }}</span>
      </div>
    </template>

    <!-- 市场卡片：命中的能力包 -->
    <template v-else>
      <ul
        v-if="card.items?.length"
        class="items"
      >
        <li
          v-for="it in card.items"
          :key="it.code"
        >
          <span class="it-name">{{ it.name }}</span>
          <span class="it-code">{{ it.code }}</span>
        </li>
      </ul>
    </template>

    <p
      v-if="card.note"
      class="note"
    >
      {{ card.note }}
    </p>

    <div
      v-if="showActions.length"
      class="actions"
    >
      <el-button
        v-for="a in showActions"
        :key="a.key"
        size="small"
        :type="a.key === 'publish' ? 'primary' : 'default'"
        :loading="publishing"
        @click="onAction(a.key)"
      >
        {{ a.label }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { declarativeApi } from '@/api/modules'
  import type { ChatCard } from '@/api/chat'
  import { useAuthStore } from '@/stores/auth'

  /**
   * 对话内结构化卡片：能力草稿（可发布）与市场能力包（可装载/已装载）。
   * 发布动作走既有能力中心闸门（前端只暴露入口，权限由后端兜底）。
   */
  const props = defineProps<{
    card: ChatCard
  }>()

  const emit = defineEmits<{
    (e: 'published', status: string): void
  }>()

  const router = useRouter()
  const auth = useAuthStore()
  const publishing = ref(false)

  const statusLabel = (s: string) => {
    const map: Record<string, string> = {
      draft: '草稿',
      pending_platform_review: '待平台审核',
      published: '已发布',
      deprecated: '已下线',
    }
    return map[s] ?? s
  }

  const stateLabel = computed(() => {
    if (props.card.type === 'market') {
      const map: Record<string, string> = {
        suggested: '待选择',
        requested: '待管理员确认',
        installed: '已装载',
        failed: '装载失败',
      }
      return map[props.card.state] ?? props.card.state
    }
    return props.card.state === 'requested' ? '待管理员确认' : '已创建草稿'
  })

  const stateTagType = computed(() => {
    if (props.card.state === 'failed') return 'danger'
    if (props.card.state === 'requested') return 'warning'
    if (props.card.state === 'installed') return 'success'
    return 'primary'
  })

  /** 动作可见性：发布只对空间管理员露出（后端仍会再校验一次） */
  const showActions = computed(() => {
    if (props.card.type !== 'capability') return props.card.actions ?? []
    return (props.card.actions ?? []).filter(a => {
      if (!a.requires_role) return true
      if (a.key === 'publish') return auth.isSpaceAdmin()
      return true
    })
  })

  async function onAction(key: string) {
    if (key === 'view') {
      await router.push('/agents')
      return
    }
    if (key !== 'publish') return
    const agentId = props.card.type === 'capability' ? props.card.agent_id : ''
    if (!agentId) return
    publishing.value = true
    try {
      const res = await declarativeApi.publish(agentId)
      const status = res?.status ?? 'published'
      emit('published', status)
      ElMessage.success(
        status === 'pending_platform_review' ? '已提交平台审核，通过后即可启用' : '已发布',
      )
    } catch {
      /* 错误 toast 已由 client 拦截器统一处理 */
    } finally {
      publishing.value = false
    }
  }
</script>

<style scoped>
  .card {
    margin-top: 8px;
    padding: 12px 14px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    display: flex;
    flex-direction: column;
    gap: 7px;
  }

  .card.market {
    border-style: dashed;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .kind {
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--ds-text-3);
  }

  .title {
    font-size: 14px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .desc {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    font-size: 12px;
    color: var(--ds-text-2);
  }

  .kv i {
    font-style: normal;
    color: var(--ds-text-3);
    margin-right: 4px;
  }

  .kv em {
    font-style: normal;
    padding: 1px 6px;
    border-radius: 6px;
    font-size: 11px;
    border: 1px solid var(--ds-border);
  }

  .kv em.low {
    color: var(--ds-success, #3f9d68);
  }

  .kv em.high {
    color: var(--ds-danger);
  }

  .tools {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .tool {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 6px;
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    color: var(--ds-text-2);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .items {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .items li {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }

  .it-code {
    font-size: 11px;
    color: var(--ds-text-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .note {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }

  .actions {
    display: flex;
    gap: 8px;
    margin-top: 2px;
  }
</style>
