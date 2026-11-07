<template>
  <div class="hub-page">
    <div class="page-head">
      <div>
        <h3>枢纽工作台</h3>
        <p class="sub">
          通用基座上的运营模式：省级科协 / 市级全域 / 实验站枢纽——聚合只出数字不出内容。
        </p>
      </div>
      <el-button
        v-if="canCreate"
        type="primary"
        @click="createVisible = true"
      >
        + 新建枢纽（选模式）
      </el-button>
    </div>

    <!-- 我可用的枢纽选择 -->
    <el-select
      v-if="hubs.length > 1"
      v-model="activeId"
      class="hub-select"
      @change="loadOverview"
    >
      <el-option
        v-for="h in hubs"
        :key="h.id"
        :value="h.id"
        :label="`${h.name}（${h.mode_label}）`"
      />
    </el-select>

    <!-- 无枢纽空态 -->
    <el-empty
      v-if="!loading && !hubs.length"
      description="你还没有加入任何枢纽。让平台管理员把你加入（或创建）一个枢纽后，这里会显示所辖小院的聚合。"
    />

    <template v-if="active">
      <!-- 模式头 -->
      <el-card class="hub-head">
        <div class="hub-title-row">
          <h4>{{ active.name }}</h4>
          <el-tag
            size="small"
            effect="plain"
            type="primary"
          >
            {{ active.mode_label }}
          </el-tag>
          <span class="muted">{{ active.region }}</span>
        </div>
        <p class="mode-focus">{{ MODE_TERMS[active.mode]?.scope || active.mode_focus }}</p>
        <div class="hub-meta">
          所辖小院 {{ active.yard_count }} 家 · 成员 {{ active.members.length }} 人
          <template v-if="isManager(active)">
            ·
            <el-button
              link
              type="primary"
              size="small"
              @click="openAttach"
            >
              关联小院
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              @click="relationVisible = true"
            >
              管理关联
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              @click="openMembers"
            >
              添加成员
            </el-button>
          </template>
        </div>
      </el-card>

      <!-- v3.67：档案没填时分组会归到「未标注」，给出可操作提示 -->
      <el-alert
        v-if="unlabeled"
        type="info"
        :closable="false"
        show-icon
      >
        <template #title>
          {{ unlabeled }} 家所辖小院未标注{{ insights?.group_label || '分组字段' }}，当前归入「未标注」组。
          <router-link
            v-if="canEditProfile"
            to="/settings?tab=yard_profile"
            class="profile-link"
          >
            去补全小院档案
          </router-link>
          <span v-else>档案由各院负责人在「系统设置 → 小院档案」补全。</span>
        </template>
      </el-alert>

      <!-- 断更预警 -->
      <el-alert
        v-if="staleCount"
        :title="`${staleCount} 家所辖小院本月日志断更（>${staleDays} 天），建议跟进`"
        type="warning"
        :closable="false"
        show-icon
      />

      <!-- v3.66：按运营模式挂载不同侧重视图（省级达标 / 市级县域 / 实验站类型） -->
      <component
        :is="panel"
        v-if="panel && insights"
        :items="items"
        :insights="insights"
      />

      <!-- 兜底：未知模式仍给通用聚合表 -->
      <el-table
        v-else
        v-col-resize
        :data="items"
        v-loading="loading"
        :default-sort="{ prop: 'journal_count', order: 'descending' }"
        empty-text="还没有绑定小院"
      >
        <el-table-column
          prop="yard_name"
          label="小院"
          min-width="180"
        />
        <el-table-column
          prop="journal_count"
          label="本月日志篇数"
          width="140"
          sortable
        />
        <el-table-column
          prop="active_authors"
          label="活跃作者"
          width="110"
          sortable
        />
        <el-table-column
          label="最近更新"
          width="170"
        >
          <template #default="{ row }">
            {{ row.last_updated_at ? formatTime(row.last_updated_at) : '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.stale ? 'danger' : 'success'"
              effect="plain"
            >
              {{ row.stale ? '断更' : '正常' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <!-- 新建枢纽（模式选择） -->
    <el-dialog
      v-model="createVisible"
      append-to-body
      title="新建枢纽 · 选择运营模式"
      width="560px"
    >
      <el-form label-width="90px">
        <el-form-item label="枢纽名称">
          <el-input
            v-model="form.name"
            maxlength="120"
            placeholder="如：曲周实验站枢纽 / 邯郸市专班"
          />
        </el-form-item>
        <el-form-item label="运营模式">
          <el-radio-group v-model="form.mode">
            <el-radio
              v-for="m in MODES"
              :key="m.key"
              :value="m.key"
              border
            >
              {{ m.label }}
            </el-radio>
          </el-radio-group>
          <div class="mode-hint">
            {{ modeHint }}
          </div>
        </el-form-item>
        <el-form-item label="区域">
          <el-input
            v-model="form.region"
            maxlength="120"
            placeholder="如：河北邯郸曲周县 / 江苏全省"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="form.note"
            type="textarea"
            :rows="2"
            maxlength="500"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="createHub"
        >
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 关联小院：下拉直选；一院可同时被多个枢纽以不同口径关联 -->
    <el-dialog
      v-model="attachVisible"
      append-to-body
      title="关联小院到本枢纽"
      width="520px"
    >
      <el-select
        v-model="attachYardId"
        v-loading="yardsLoading"
        filterable
        placeholder="选择小院"
        class="attach-select"
      >
        <el-option
          v-for="y in attachableYards"
          :key="y.id"
          :value="y.id"
          :label="y.name"
          :disabled="y.in_current_hub"
        >
          <span>{{ y.name }}</span>
          <span class="opt-hint">{{ yardHint(y) }}</span>
        </el-option>
      </el-select>

      <div class="relation-row">
        <span class="relation-label">关系口径</span>
        <el-radio-group v-model="attachRelation">
          <el-radio
            v-for="r in RELATION_OPTIONS"
            :key="r.key"
            :value="r.key"
          >
            {{ r.label }}
          </el-radio>
        </el-radio-group>
      </div>
      <p class="relation-note">
        {{ RELATION_OPTIONS.find(r => r.key === attachRelation)?.hint }}
      </p>

      <template #footer>
        <el-button @click="attachVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="attaching"
          :disabled="!attachYardId"
          @click="attach"
        >
          关联
        </el-button>
      </template>
    </el-dialog>

    <!-- 管理关联：解除本枢纽与该院的关系（不动其他枢纽） -->
    <el-dialog
      v-model="relationVisible"
      append-to-body
      title="管理本枢纽的小院关联"
      width="560px"
    >
      <el-table
        v-col-resize
        :data="items"
        size="small"
        empty-text="还没有关联小院"
      >
        <el-table-column
          prop="yard_name"
          label="小院"
          min-width="160"
        />
        <el-table-column
          label="关系"
          width="110"
        >
          <template #default="{ row }">
            {{ row.relation_label || '日常管理' }}
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="90"
        >
          <template #default="{ row }">
            <el-button
              link
              type="danger"
              size="small"
              :loading="detachingId === row.yard_id"
              @click="detach(row)"
            >
              解除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="relationVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 添加成员：下拉直选，无需手动找用户 ID -->
    <el-dialog
      v-model="memberVisible"
      append-to-body
      title="添加枢纽成员"
      width="480px"
    >
      <el-select
        v-model="memberUserId"
        v-loading="candidatesLoading"
        filterable
        placeholder="选择要添加的成员"
        class="mb"
      >
        <el-option
          v-for="c in memberCandidates"
          :key="c.id"
          :value="c.id"
          :label="c.display_name"
        >
          <span>{{ c.display_name }}</span>
          <span class="opt-hint">@{{ c.username }}</span>
        </el-option>
      </el-select>
      <el-radio-group v-model="memberRole">
        <el-radio value="manager">manager（可管理）</el-radio>
        <el-radio value="viewer">viewer（只看聚合）</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="memberVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="adding"
          @click="addMember"
        >
          添加
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { hubsApi, type AttachableYard, type HubInsights, type HubOverviewItem, type HubRow } from '@/api/hubs'
  import { panelFor, MODE_TERMS } from '@/components/hubs/panels'
  import { formatTime } from '@/utils/format'
  import { useAuthStore } from '@/stores/auth'

  /**
   * 枢纽工作台（v3.65）：通用基座上的运营模式选择——
   * 省级科协（province）/ 市级全域（municipal）/ 实验站枢纽（station）。
   * 聚合只出数字与断更预警，不含日志内容。
   */
  const auth = useAuthStore()
  const hubs = ref<HubRow[]>([])
  const activeId = ref('')
  const items = ref<HubOverviewItem[]>([])
  const insights = ref<HubInsights | null>(null)
  const staleDays = ref(7)
  const staleCount = ref(0)
  const loading = ref(false)

  const createVisible = ref(false)
  const creating = ref(false)
  const attachVisible = ref(false)
  const attaching = ref(false)
  const attachYardId = ref('')
  const attachRelation = ref<'manage' | 'review' | 'observe'>('manage')
  const relationVisible = ref(false)
  const detachingId = ref('')
  const memberVisible = ref(false)
  const adding = ref(false)
  const memberUserId = ref('')
  const memberRole = ref<'manager' | 'viewer'>('viewer')

  const MODES = [
    { key: 'province', label: '省级科协模式', hint: '小院跨市分散，按培养单位/联络站聚合，重达标监测。' },
    { key: 'municipal', label: '市级全域模式', hint: '一市全域推广、县域全覆盖，重推广进度与断更跟进。' },
    { key: 'station', label: '实验站枢纽模式', hint: '实体实验站统管小院群，重横向对比与一院一策。' },
  ]
  /** v3.67：枢纽与小院的关系口径——一个院可同时被多个枢纽关联 */
  const RELATION_OPTIONS = [
    { key: 'manage', label: '日常管理', hint: '实验站/市专班统管该院：计入所辖、可作为主归属。' },
    { key: 'review', label: '评审归口', hint: '省科协评价用：只看聚合数字，不干预小院内部管理。' },
    { key: 'observe', label: '观察', hint: '示范跟踪等只读纳入，不改变主归属。' },
  ]
  const form = ref({ name: '', mode: 'station', region: '', note: '' })
  const modeHint = computed(
    () => MODES.find(m => m.key === form.value.mode)?.hint ?? '',
  )

  const active = computed(() => hubs.value.find(h => h.id === activeId.value) ?? null)
  /** v3.66：按运营模式挂载不同视图；未知模式回落到通用表 */
  const panel = computed(() => panelFor(active.value?.mode))
  /** v3.67：未标注分组的小院数（档案没填 → 分组归到「未标注」） */
  const unlabeled = computed(() => insights.value?.summary?.unlabeled ?? 0)
  const canEditProfile = computed(() => auth.hasPermission('settings:manage'))
  const canCreate = computed(() => auth.roles?.includes('platform_admin') || auth.roles?.includes('yard_owner'))
  function isManager(h: HubRow): boolean {
    // 后端已按「hub 成员 / 所辖小院管理者 / 平台管理员」算好 my_role
    return h.my_role === 'manager' || auth.roles?.includes('platform_admin') === true
  }

  async function loadHubs(): Promise<void> {
    try {
      const data = await hubsApi.list()
      hubs.value = data.items
      if (!activeId.value && data.items.length) activeId.value = data.items[0].id
      if (activeId.value) await loadOverview()
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function loadOverview(): Promise<void> {
    if (!activeId.value) return
    loading.value = true
    try {
      const data = await hubsApi.overview(activeId.value)
      items.value = data.items
      insights.value = data.insights ?? null
      staleDays.value = data.stale_days
      staleCount.value = data.stale_count
    } catch {
      /* toast 已统一处理 */
    } finally {
      loading.value = false
    }
  }

  async function createHub(): Promise<void> {
    creating.value = true
    try {
      const hub = await hubsApi.create({ ...form.value })
      ElMessage.success(`枢纽已创建（${hub.mode_label}）`)
      createVisible.value = false
      form.value = { name: '', mode: form.value.mode, region: '', note: '' }
      await loadHubs()
      activeId.value = hub.id
      await loadOverview()
    } catch {
      /* toast 已统一处理 */
    } finally {
      creating.value = false
    }
  }

  const attachableYards = ref<AttachableYard[]>([])
  const yardsLoading = ref(false)

  /** 候选行提示：已在本枢纽 / 已属他处（仍可按评审关系加入） */
  function yardHint(y: AttachableYard): string {
    if (y.in_current_hub) {
      const rel = y.relations.find(r => r.hub_id === activeId.value)
      return `已在本枢纽（${rel?.relation_label ?? '已关联'}）`
    }
    const others = y.relations.filter(r => r.hub_id !== activeId.value)
    if (!others.length) return ''
    return `已属：${others.map(r => `${r.hub_name ?? '其他枢纽'}（${r.relation_label}）`).join('、')}`
  }

  async function openAttach(): Promise<void> {
    attachVisible.value = true
    attachRelation.value = 'manage'
    yardsLoading.value = true
    try {
      attachableYards.value = await hubsApi.attachableYards(activeId.value)
    } catch {
      /* toast 已统一处理 */
    } finally {
      yardsLoading.value = false
    }
  }

  async function attach(): Promise<void> {
    attaching.value = true
    try {
      const res = await hubsApi.attach(activeId.value, attachYardId.value, attachRelation.value)
      ElMessage.success(res.primary ? '小院已关联并成为主归属' : '小院已关联（主归属不变）')
      attachVisible.value = false
      attachYardId.value = ''
      await loadOverview()
    } catch {
      /* toast 已统一处理 */
    } finally {
      attaching.value = false
    }
  }

  async function detach(row: unknown): Promise<void> {
    const yardId = (row as HubOverviewItem).yard_id
    detachingId.value = yardId
    try {
      const res = await hubsApi.detach(activeId.value, yardId)
      ElMessage.success(
        res.hub_id
          ? `已解除本枢纽关联，该院主归属移交给其他枢纽`
          : '已解除关联',
      )
      await loadOverview()
    } catch {
      /* toast 已统一处理 */
    } finally {
      detachingId.value = ''
    }
  }

  const memberCandidates = ref<{ id: string; username: string; display_name: string }[]>([])
  const candidatesLoading = ref(false)

  async function openMembers(): Promise<void> {
    memberVisible.value = true
    candidatesLoading.value = true
    memberUserId.value = ''
    try {
      memberCandidates.value = await hubsApi.memberCandidates(activeId.value)
    } catch {
      /* toast 已统一处理 */
    } finally {
      candidatesLoading.value = false
    }
  }

  async function addMember(): Promise<void> {
    adding.value = true
    try {
      await hubsApi.addMember(activeId.value, memberUserId.value, memberRole.value)
      ElMessage.success('成员已添加')
      memberVisible.value = false
      memberUserId.value = ''
      await loadHubs()
    } catch {
      /* toast 已统一处理 */
    } finally {
      adding.value = false
    }
  }

  onMounted(() => {
    void loadHubs()
  })
</script>

<style scoped>
  .profile-link {
    color: var(--ds-brand);
    text-decoration: underline;
    margin-left: 4px;
  }
  .hub-page {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .page-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .page-head h3 {
    margin: 0 0 4px;
  }
  .sub {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
  .hub-select {
    max-width: 360px;
  }
  .hub-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .hub-title-row h4 {
    margin: 0;
    font-size: 15px;
  }
  .muted {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .mode-focus {
    margin: 6px 0;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }
  .hub-meta {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .mode-hint {
    font-size: 12px;
    color: var(--ds-text-3);
    margin-top: 6px;
  }
  .mb {
    margin-bottom: 10px;
  }
  .attach-select {
    width: 100%;
  }
  .opt-hint {
    float: right;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .relation-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .relation-label {
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .relation-note {
    margin: 6px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
  }
</style>
