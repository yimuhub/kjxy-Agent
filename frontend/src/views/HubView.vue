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
        <p class="mode-focus">{{ active.mode_focus }}</p>
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
              绑定小院
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

      <!-- 断更预警 -->
      <el-alert
        v-if="staleCount"
        :title="`${staleCount} 家所辖小院本月日志断更（>${staleDays} 天），建议跟进`"
        type="warning"
        :closable="false"
        show-icon
      />

      <!-- 所辖小院聚合表 -->
      <el-table
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

    <!-- 绑定小院：下拉直选，无需手动找 ID -->
    <el-dialog
      v-model="attachVisible"
      title="绑定小院到本枢纽"
      width="480px"
    >
      <el-select
        v-model="attachYardId"
        v-loading="yardsLoading"
        filterable
        placeholder="选择你要绑定的小院"
        class="attach-select"
      >
        <el-option
          v-for="y in attachableYards"
          :key="y.id"
          :value="y.id"
          :label="y.name"
          :disabled="!!y.hub_id"
        >
          <span>{{ y.name }}</span>
          <span class="opt-hint">
            {{ y.hub_id ? (y.hub_name ? `已归属：${y.hub_name}` : '已归属其他枢纽') : '' }}
          </span>
        </el-option>
      </el-select>
      <template #footer>
        <el-button @click="attachVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="attaching"
          @click="attach"
        >
          绑定
        </el-button>
      </template>
    </el-dialog>

    <!-- 添加成员：下拉直选，无需手动找用户 ID -->
    <el-dialog
      v-model="memberVisible"
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
  import { hubsApi, type HubRow } from '@/api/hubs'
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
  const items = ref<
    { yard_id: string; yard_name: string; journal_count: number; active_authors: number; last_updated_at: string | null; stale: boolean }[]
  >([])
  const staleDays = ref(7)
  const staleCount = ref(0)
  const loading = ref(false)

  const createVisible = ref(false)
  const creating = ref(false)
  const attachVisible = ref(false)
  const attaching = ref(false)
  const attachYardId = ref('')
  const memberVisible = ref(false)
  const adding = ref(false)
  const memberUserId = ref('')
  const memberRole = ref<'manager' | 'viewer'>('viewer')

  const MODES = [
    { key: 'province', label: '省级科协模式', hint: '小院跨市分散，按培养单位/联络站聚合，重达标监测。' },
    { key: 'municipal', label: '市级全域模式', hint: '一市全域推广、县域全覆盖，重推广进度与断更跟进。' },
    { key: 'station', label: '实验站枢纽模式', hint: '实体实验站统管小院群，重横向对比与一院一策。' },
  ]
  const form = ref({ name: '', mode: 'station', region: '', note: '' })
  const modeHint = computed(
    () => MODES.find(m => m.key === form.value.mode)?.hint ?? '',
  )

  const active = computed(() => hubs.value.find(h => h.id === activeId.value) ?? null)
  const canCreate = computed(() => auth.roles?.includes('platform_admin') || auth.roles?.includes('yard_owner'))
  function isManager(h: HubRow): boolean {
    return (
      auth.roles?.includes('platform_admin') ||
      h.members.some(m => m.user_id === (auth.user?.user_id ?? auth.user?.id ?? '') && m.role === 'manager')
    )
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

  const attachableYards = ref<{ id: string; name: string; hub_id: string | null; hub_name: string | null }[]>([])
  const yardsLoading = ref(false)

  async function openAttach(): Promise<void> {
    attachVisible.value = true
    yardsLoading.value = true
    try {
      attachableYards.value = await hubsApi.attachableYards()
    } catch {
      /* toast 已统一处理 */
    } finally {
      yardsLoading.value = false
    }
  }

  async function attach(): Promise<void> {
    attaching.value = true
    try {
      await hubsApi.attach(activeId.value, attachYardId.value)
      ElMessage.success('小院已绑定到枢纽')
      attachVisible.value = false
      attachYardId.value = ''
      await loadOverview()
    } catch {
      /* toast 已统一处理 */
    } finally {
      attaching.value = false
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
</style>
