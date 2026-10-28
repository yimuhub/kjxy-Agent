<template>
  <section
    class="scope-panel"
    :aria-label="`${scopeTitle}技能列表`"
  >
    <div class="scope-bar">
      <span class="muted">
        {{ state === 'loading' ? '加载中…' : `共 ${total} 个${scopeTitle}技能` }}
      </span>
      <div class="scope-bar-actions">
        <el-button
          v-if="scope === 'mine'"
          size="small"
          :loading="state === 'loading'"
          @click="emit('create')"
        >
          一句话创建
        </el-button>
        <el-button
          size="small"
          :loading="state === 'loading'"
          aria-label="刷新技能列表"
          @click="load()"
        >
          刷新
        </el-button>
      </div>
    </div>

    <AsyncState
      :state="state"
      :empty-text="emptyText"
      :empty-action-text="scope === 'mine' ? '一句话创建技能' : ''"
      :error-text="errorText"
      @retry="load()"
      @action="emit('create')"
    >
      <div class="card-grid">
        <article
          v-for="skill in items"
          :key="skill.id"
          class="skill-card glass-panel"
          :aria-label="`${skillName(skill)}，${visibilityLabel(skill.visibility)}，${skillStatusLabel(skill.status)}`"
        >
          <header class="card-head">
            <h3 class="card-name">
              {{ skillName(skill) }}
            </h3>
            <div class="badges">
              <el-tag
                size="small"
                :type="visibilityTagType(skill.visibility)"
                effect="plain"
                :title="visibilityTip(skill.visibility)"
              >
                {{ visibilityLabel(skill.visibility) }}
              </el-tag>
              <el-tag
                size="small"
                :type="skillStatusTagType(skill.status)"
                effect="plain"
              >
                {{ skillStatusLabel(skill.status) }}
              </el-tag>
              <el-tag
                size="small"
                :type="skillRiskMeta(skill).type"
                effect="plain"
              >
                {{ skillRiskMeta(skill).label }}
              </el-tag>
              <el-tag
                v-if="skill.signed_at"
                size="small"
                type="success"
                effect="plain"
                :title="`定义摘要 ${digestShort(skill.definition_sha256)}；签名算法 ${skill.signature_alg || '—'}`"
              >
                已签名
              </el-tag>
              <el-tag
                v-else-if="skill.status === 'published'"
                size="small"
                type="info"
                effect="plain"
                title="该版本发布时未生成签名（3.17 之前的存量版本）"
              >
                未签名
              </el-tag>
            </div>
          </header>

          <p
            v-if="skillSummary(skill)"
            class="card-desc"
          >
            {{ skillSummary(skill) }}
          </p>
          <p
            v-else
            class="card-desc muted"
          >
            暂无摘要
          </p>

          <dl class="card-meta">
            <div>
              <dt>来源</dt>
              <dd>{{ skillOriginLabel(skill) }}</dd>
            </div>
            <div>
              <dt>编码</dt>
              <dd :title="skillCode(skill) || '—'">
                {{ skillCode(skill) || '—' }}
              </dd>
            </div>
            <div>
              <dt>版本</dt>
              <dd>{{ skill.version || '—' }}</dd>
            </div>
          </dl>

          <p
            v-if="skill.review_note && skill.status === 'rejected'"
            class="review-note"
          >
            审核意见：{{ skill.review_note }}
          </p>

          <footer class="card-actions">
            <!-- 草稿：提交小院公共库审核 -->
            <el-button
              v-if="scope === 'mine' && isDraft(skill)"
              type="primary"
              size="small"
              :loading="actingId === skill.id && acting === 'submit'"
              @click="submitSkill(skill)"
            >
              提交审核
            </el-button>

            <!-- 审核中：只提示，不提供前端「撤回」（后端无撤回接口） -->
            <span
              v-else-if="scope === 'mine' && skill.status === 'pending_review'"
              class="hint"
            >
              已提交，等待负责人审核
            </span>

            <!-- 已发布：提示去助手编辑里绑定 -->
            <el-button
              v-else-if="skill.status === 'published' && skillVisibility(skill) !== 'platform'"
              size="small"
              @click="goBind"
            >
              绑定到助手
            </el-button>

            <span
              v-else
              class="hint"
            >—</span>

            <!-- 负责人特权：私有 → 小院公共 -->
            <el-button
              v-if="canPromote && skillVisibility(skill) === 'private'"
              link
              type="warning"
              :loading="actingId === skill.id && acting === 'promote'"
              @click="promoteSkill(skill)"
            >
              转为小院公共
            </el-button>
          </footer>
        </article>
      </div>

      <div
        v-if="total > pageSize"
        class="pager"
      >
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          background
          small
          @current-change="onPageChange"
        />
      </div>
    </AsyncState>
  </section>
</template>

<script setup lang="ts">
  import { computed, onUnmounted, ref, watch } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import AsyncState from '@/components/AsyncState.vue'
  import { skillApi, skillApiErrorText, type SkillScope } from '@/api/v3'
  import type { Skill } from '@/types/api'
  import { classifyError, type ViewState } from '@/utils/status'
  import {
    skillCode,
    skillName,
    skillOriginLabel,
    skillRiskMeta,
    skillStatusLabel,
    skillStatusTagType,
    skillSummary,
    skillVisibility,
    visibilityLabel,
    visibilityTagType,
    visibilityTip,
  } from './skillMeta'

  /**
   * 技能归属视图：我的 / 小院 / 平台。
   *
   * 数据一律来自 GET /skills?scope=...；后端未就绪（404）时由 AsyncState 展示
   * 「加载失败 + 重试」，不抛整页错误、不弹全局 toast（本组接口走静默请求）。
   */
  const props = withDefaults(
    defineProps<{
      scope: SkillScope
      /** 负责人（yard_owner）才看得到「转为小院公共」 */
      canPromote?: boolean
      pageSize?: number
    }>(),
    { canPromote: false, pageSize: 12 },
  )

  const emit = defineEmits<{
    (e: 'create'): void
  }>()

  const router = useRouter()
  const silent = { skipErrorToast: true }

  const items = ref<Skill[]>([])
  const total = ref(0)
  const page = ref(1)
  const state = ref<ViewState>('loading')
  const errorText = ref('')
  const actingId = ref('')
  const acting = ref<'submit' | 'promote' | ''>('')

  /** 请求序号：切换 scope / 翻页过快时丢弃过期响应 */
  let requestId = 0
  let disposed = false

  const scopeTitle = computed(() => {
    if (props.scope === 'mine') return '我创建的'
    if (props.scope === 'yard') return '小院共享的'
    return '平台共享的'
  })

  const emptyText = computed(() => {
    if (props.scope === 'mine') return '还没有你创建的技能，试试「一句话创建技能」'
    if (props.scope === 'yard') return '小院还没有共享技能'
    return '平台还没有共享技能'
  })

  function isDraft(skill: Skill): boolean {
    return skill.status === 'draft' || skill.status === 'testing'
  }

  function digestShort(digest?: string | null): string {
    return digest ? `${digest.slice(0, 12)}…` : '—'
  }

  async function load(nextPage = page.value) {
    const current = ++requestId
    state.value = 'loading'
    errorText.value = ''
    try {
      const res = await skillApi.list({
        scope: props.scope,
        page: nextPage,
        pageSize: props.pageSize,
      })
      if (disposed || current !== requestId) return
      items.value = res.items
      total.value = res.total || res.items.length
      page.value = nextPage
      state.value = res.items.length ? 'ready' : 'empty'
    } catch (e) {
      if (disposed || current !== requestId) return
      errorText.value = skillApiErrorText(e, '技能列表')
      state.value = classifyError(e)
    }
  }

  function onPageChange(next: number) {
    void load(next)
  }

  async function submitSkill(skill: Skill) {
    if (actingId.value) return
    actingId.value = skill.id
    acting.value = 'submit'
    try {
      await skillApi.review(skill.id, 'submit_public', undefined, silent)
      ElMessage.success('已提交审核，负责人通过后会转为小院公共')
      await load()
    } catch (e) {
      ElMessage.error(skillApiErrorText(e, '提交审核'))
    } finally {
      actingId.value = ''
      acting.value = ''
    }
  }

  async function promoteSkill(skill: Skill) {
    if (actingId.value) return
    try {
      await ElMessageBox.confirm(
        `将「${skillName(skill)}」转为小院公共并直接发布？转换后本小院全体成员可用，系统会通知技能创建者。`,
        '转为小院公共',
        { type: 'warning', confirmButtonText: '确认转换', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    actingId.value = skill.id
    acting.value = 'promote'
    try {
      await skillApi.promote(skill.id, silent)
      ElMessage.success('已转为小院公共，并已通知创建者')
      await load()
    } catch (e) {
      ElMessage.error(skillApiErrorText(e, '转为小院公共'))
    } finally {
      actingId.value = ''
      acting.value = ''
    }
  }

  function goBind() {
    void router.push('/agents/custom')
  }

  watch(
    () => props.scope,
    () => {
      page.value = 1
      void load(1)
    },
    { immediate: true },
  )

  onUnmounted(() => {
    disposed = true
  })

  defineExpose({
    /** 供父级（创建向导成功后）刷新当前空间 */
    reload: () => load(),
  })
</script>

<style scoped>
  .scope-panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .scope-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .scope-bar-actions {
    display: flex;
    gap: 8px;
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
  }
  .skill-card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px;
    min-height: 196px;
  }
  .card-head {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .card-name {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    line-height: 1.4;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .card-desc {
    margin: 0;
    flex: 1;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .card-meta {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin: 0;
    padding-top: 10px;
    border-top: 1px solid var(--ds-border);
  }
  .card-meta div {
    min-width: 0;
  }
  .card-meta dt {
    margin-bottom: 4px;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .card-meta dd {
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .review-note {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-danger);
  }
  .card-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
    padding-top: 4px;
  }
  .hint {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .pager {
    display: flex;
    justify-content: center;
    padding-top: 4px;
  }
</style>
