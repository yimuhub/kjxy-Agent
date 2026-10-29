<template>
  <div class="library">
    <div class="page-header">
      <div class="header-title">
        <h2 class="page-title">
          插件与技能库
        </h2>
        <p class="sub">
          检索内置、GitHub 与本地技能/插件；导入与安装默认进入专家审核，审核通过后启用。
        </p>
      </div>
      <div class="page-actions">
        <el-tag
          class="net-badge"
          :type="netDisplay.type"
          effect="plain"
          :title="netTitle"
          aria-live="polite"
        >
          <span
            class="net-dot"
            :class="netMode"
          />
          {{ netDisplay.label }}
        </el-tag>
        <el-button
          v-if="canGenerate"
          type="primary"
          aria-label="一句话创建技能"
          @click="openWizard('skill')"
        >
          一句话创建技能
        </el-button>
        <el-button
          v-if="canGenerate"
          aria-label="一句话生成插件"
          @click="openWizard('plugin')"
        >
          一句话生成插件
        </el-button>
        <el-button
          :loading="skillImporting"
          aria-label="上传本地 Skill 文件"
          @click="pickSkillFile"
        >
          上传 Skill 文件
        </el-button>
        <el-button
          :loading="pluginImporting"
          aria-label="导入本地插件 zip 包"
          @click="pickPluginFile"
        >
          导入插件 zip
        </el-button>
      </div>
    </div>

    <SkillWizard
      v-model="wizardOpen"
      :kind="wizardKind"
      @created="onSkillCreated"
    />

    <input
      ref="skillFileInput"
      class="hidden-file"
      type="file"
      accept=".md,.markdown,.txt"
      aria-label="选择 Skill 文件"
      @change="handleSkillImport"
    >
    <input
      ref="pluginFileInput"
      class="hidden-file"
      type="file"
      accept=".zip"
      aria-label="选择插件 zip 包"
      @change="handlePluginImport"
    >

    <el-alert
      v-if="isTrulyOffline"
      class="offline-alert"
      type="warning"
      show-icon
      :closable="false"
    >
      <div class="offline-body">
        <span class="offline-text">{{ OFFLINE_SEARCH_TIP }}</span>
        <el-button
          size="small"
          type="primary"
          plain
          aria-label="打开本地 Skill 管理"
          @click="openLocalSkillManagement"
        >
          打开本地 Skill 管理
        </el-button>
      </div>
    </el-alert>
    <el-alert
      v-else-if="isDegradedNet"
      class="degraded-alert"
      type="warning"
      show-icon
      :closable="false"
      :title="DEGRADED_SEARCH_TIP"
    />

    <el-tabs
      v-model="activeTab"
      class="library-tabs"
      @tab-click="onTabClick"
    >
      <el-tab-pane
        label="技能库"
        name="skills"
      />
      <el-tab-pane
        label="插件库"
        name="plugins"
      />
      <el-tab-pane
        label="已安装"
        name="installed"
      />
    </el-tabs>

    <template v-if="activeTab !== 'installed'">
      <!-- 技能区归属视图：市场检索 / 我的 / 小院 / 平台（v3.2） -->
      <el-tabs
        v-if="activeTab === 'skills'"
        v-model="scopeTab"
        class="scope-tabs"
        @tab-change="onScopeChange"
      >
        <el-tab-pane
          label="市场"
          name="market"
        />
        <el-tab-pane
          label="我的"
          name="mine"
        />
        <el-tab-pane
          label="小院"
          name="yard"
        />
        <el-tab-pane
          label="平台"
          name="platform"
        />
      </el-tabs>

      <template v-if="showMarket">
        <div class="filter-panel glass-panel">
          <el-input
            v-model="query"
            class="search-input"
            placeholder="搜索名称、编码或描述"
            clearable
            :prefix-icon="Search"
            aria-label="搜索插件与技能"
            @keyup.enter="submitSearch"
            @clear="onSearchClear"
          />
          <el-select
            v-model="sourceFilter"
            class="filter-select"
            aria-label="按来源筛选"
            @change="onSourceChange"
          >
            <el-option
              label="全部来源"
              value="all"
            />
            <el-option
              label="内置"
              value="builtin"
            />
            <el-option
              label="GitHub"
              value="github"
            />
            <el-option
              label="本地"
              value="local"
            />
          </el-select>
          <el-select
            v-model="typeFilter"
            class="filter-select"
            aria-label="按类型筛选"
            @change="onTypeChange"
          >
            <el-option
              label="全部类型"
              value="all"
            />
            <el-option
              label="技能"
              value="skill"
            />
            <el-option
              label="插件"
              value="plugin"
            />
          </el-select>
          <div class="filter-actions">
            <el-button
              type="primary"
              @click="submitSearch"
            >
              搜索
            </el-button>
            <el-button
              :loading="githubLoading"
              aria-label="搜索 GitHub 技能"
              @click="runGithubSearch"
            >
              搜索 GitHub
            </el-button>
          </div>
        </div>

        <div
          v-if="listHint"
          class="inline-hint"
          role="status"
          aria-live="polite"
        >
          {{ listHint }}
        </div>

        <AsyncState
          :state="listState"
          :empty-text="listEmptyText"
          :empty-action-text="listEmptyAction"
          :error-text="listError"
          @retry="loadItems"
          @action="onEmptyAction"
        >
          <div class="card-grid">
            <LibraryItemCard
              v-for="item in items"
              :key="item.item_key"
              :item="item"
              :installing="installingKey === item.item_key"
              @preview="openPreview(item)"
              @install="installFromCard(item)"
            />
          </div>
        </AsyncState>
      </template>

      <SkillScopePanel
        v-else
        ref="scopePanel"
        :scope="scopeTab === 'market' ? 'mine' : scopeTab"
        :can-promote="isYardOwner"
        @create="openWizard"
      />
    </template>

    <template v-else>
      <div class="installed-bar">
        <span class="muted">共 {{ installed.length }} 个已安装条目</span>
        <el-button
          size="small"
          :loading="installedState === 'loading'"
          aria-label="刷新已安装列表"
          @click="loadInstalled(true)"
        >
          刷新
        </el-button>
      </div>
      <AsyncState
        :state="installedState"
        empty-text="本小院还没有安装或导入任何插件/技能"
        empty-action-text="去技能库看看"
        :error-text="installedError"
        @retry="loadInstalled(true)"
        @action="goSkillsTab"
      >
        <el-table
          :data="installed"
          row-key="key"
          stripe
        >
          <el-table-column
            label="名称"
            min-width="220"
          >
            <template #default="{ row }">
              <div class="installed-name">
                {{ row.name }}
              </div>
              <div class="installed-code">
                {{ row.code || '—' }}
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="类型"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                effect="plain"
                :type="row.type === 'plugin' ? 'warning' : 'info'"
              >
                {{ typeLabel(row.type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            prop="version"
            label="版本"
            width="110"
          />
          <el-table-column
            label="状态"
            width="120"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="statusTagType(row.status)"
              >
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="来源"
            width="110"
          >
            <template #default="{ row }">
              {{ sourceLabel(row.source) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="100"
          >
            <template #default="{ row }">
              <el-button
                v-if="row.item_key"
                link
                type="primary"
                :aria-label="`预览 ${row.name}`"
                @click="openInstalledPreview(row as InstalledItem)"
              >
                预览
              </el-button>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
      </AsyncState>
    </template>

    <el-dialog
      v-model="drawerOpen"
      append-to-body
      :title="previewTitle"
      width="min(520px, 92vw)"
      destroy-on-close
      align-center
    >
      <AsyncState
        :state="previewState"
        :error-text="previewError"
        @retry="retryPreview"
      >
        <div
          v-if="previewData"
          class="preview"
        >
          <p class="preview-desc">
            {{ previewData.description || '暂无描述' }}
          </p>
          <el-descriptions
            :column="1"
            border
            size="small"
          >
            <el-descriptions-item label="类型">
              {{ typeLabel(previewData.item_type) }}
            </el-descriptions-item>
            <el-descriptions-item label="来源">
              {{ sourceLabel(previewData.source) }}
            </el-descriptions-item>
            <el-descriptions-item label="许可证">
              {{ previewData.license || '未声明' }}
            </el-descriptions-item>
            <el-descriptions-item label="风险">
              <el-tag
                size="small"
                :type="riskTagType(previewData.risk_level)"
                effect="plain"
              >
                {{ riskLabel(previewData.risk_level) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="版本">
              {{ previewData.version || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="作者">
              {{ previewData.author || '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="来源链接">
              <a
                v-if="previewSourceUrl"
                :href="previewSourceUrl"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ previewSourceUrl }}
              </a>
              <span v-else>{{ previewData.source_url || '—' }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <section class="preview-section">
            <h4>定义摘要</h4>
            <pre
              v-if="previewPrompt"
              class="code-block"
            >{{ previewPrompt }}</pre>
            <p
              v-else
              class="muted"
            >
              暂无定义摘要
            </p>
          </section>

          <section
            v-if="previewManifest"
            class="preview-section"
          >
            <h4>Manifest 摘要</h4>
            <pre class="code-block">{{ previewManifest }}</pre>
          </section>
        </div>
      </AsyncState>

      <template #footer>
        <div class="drawer-footer">
          <el-button @click="drawerOpen = false">
            关闭
          </el-button>
          <el-button
            v-if="previewData"
            type="primary"
            :loading="installingKey === previewData.item_key"
            :disabled="previewInstalled"
            :aria-label="`${previewInstalled ? '已安装' : isPlugin(previewData) ? '安装并提交审核' : '安装'} ${previewData.name}`"
            @click="installPreview"
          >
            {{
              previewInstalled
                ? '已安装'
                : isPlugin(previewData)
                  ? '安装并提交审核'
                  : '安装'
            }}
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { Search } from '@element-plus/icons-vue'
  import { libraryApi, searchApi, type MarketItem, type SkillScope } from '@/api/v3'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import LibraryItemCard from '@/components/library/LibraryItemCard.vue'
  import SkillScopePanel from '@/components/library/SkillScopePanel.vue'
  import SkillWizard from '@/components/library/SkillWizard.vue'
  import {
    definitionExcerpt,
    definitionOf,
    importSkillMessage,
    installNeedsReview,
    isPlugin,
    itemInstalled,
    manifestExcerpt,
    normalizeInstalled,
    normalizeSearchItems,
    riskLabel,
    riskTagType,
    sourceLabel,
    statusLabel,
    statusTagType,
    type ImportResultLike,
    type InstalledItem,
    type InstallResultLike,
    type LibraryItem,
    type MarketSearchResponse,
    typeLabel,
  } from '@/components/library/libraryMeta'
  import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
  import {
    DEGRADED_SEARCH_TIP,
    OFFLINE_SEARCH_TIP,
    hasPermission,
  } from '@/components/settings/permissions'
  import { useAuthStore } from '@/stores/auth'

  type TabName = 'skills' | 'plugins' | 'installed'
  type ScopeTab = 'market' | SkillScope
  type NetMode = 'online' | 'degraded' | 'offline' | 'unknown'
  type SourceFilter = 'all' | 'builtin' | 'github' | 'local'
  type TypeFilter = 'all' | 'skill' | 'plugin'

  const NET_META: Record<NetMode, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
    online: { label: '在线', type: 'success' },
    degraded: { label: '弱网', type: 'warning' },
    offline: { label: '离线', type: 'danger' },
    unknown: { label: '状态未知', type: 'info' },
  }

  const router = useRouter()
  const auth = useAuthStore()

  const activeTab = ref<TabName>('skills')
  /** 技能区归属视图：market=市场检索（保持原有行为），其余对应 GET /skills?scope= */
  const scopeTab = ref<ScopeTab>('market')
  const query = ref('')
  const sourceFilter = ref<SourceFilter>('builtin')
  const typeFilter = ref<TypeFilter>('skill')

  const wizardOpen = ref(false)
  /** 向导当前模式：技能走「检索候选 → 生成」，插件直接按工作流模板生成 */
  const wizardKind = ref<'skill' | 'plugin'>('skill')
  const scopePanel = ref<InstanceType<typeof SkillScopePanel> | null>(null)

  /** 一句话创建入口：无 skills:generate 的角色直接隐藏（后端仍会校验） */
  const canGenerate = computed(() => hasPermission(auth.roles, 'skills:generate'))
  /** 私有技能转小院公共是负责人特权 */
  const isYardOwner = computed(
    () => auth.roles.includes('yard_owner') || auth.roles.includes('platform_admin'),
  )
  /** 当前是否展示市场检索区（插件库同样复用该区块） */
  const showMarket = computed(
    () => activeTab.value === 'plugins' || scopeTab.value === 'market',
  )

  const items = ref<LibraryItem[]>([])
  const listState = ref<ViewState>('loading')
  const listError = ref('')
  const listHint = ref('')
  const installingKey = ref('')
  const githubLoading = ref(false)
  const skillImporting = ref(false)
  const pluginImporting = ref(false)

  const installed = ref<InstalledItem[]>([])
  const installedState = ref<ViewState>('loading')
  const installedError = ref('')
  const installedLoaded = ref(false)

  const netMode = ref<NetMode>('unknown')
  const netLoading = ref(true)
  const netStatus = ref<Awaited<ReturnType<typeof searchApi.status>> | null>(null)

  const skillFileInput = ref<HTMLInputElement | null>(null)
  const pluginFileInput = ref<HTMLInputElement | null>(null)

  const drawerOpen = ref(false)
  const previewSummary = ref<LibraryItem | null>(null)
  const previewDetail = ref<LibraryItem | null>(null)
  const previewKey = ref('')
  const previewState = ref<ViewState>('loading')
  const previewError = ref('')

  let listRequestId = 0
  let disposed = false

  const netDisplay = computed(() =>
    netLoading.value ? { label: '检测中', type: 'info' as const } : NET_META[netMode.value],
  )
  const netTitle = computed(() => {
    if (netLoading.value) return '正在检测联网状态'
    const parts = [netStatus.value?.provider, netStatus.value?.reason].filter(Boolean)
    return parts.length ? parts.join(' · ') : '联网状态未知'
  })
  /**
   * 真离线：mode=offline 且后端确认无互联网。
   * internet 字段缺失（老后端）视为未知，按降级处理，不显示「本地部署形态」文案。
   */
  const isTrulyOffline = computed(
    () => netMode.value === 'offline' && netStatus.value?.internet === false,
  )
  /** 联网受限：明确 degraded，或 offline 但互联网状态未知/矛盾 —— 轻提示，不阻断浏览与本地导入 */
  const isDegradedNet = computed(
    () =>
      !isTrulyOffline.value &&
      (netMode.value === 'degraded' ||
        (netMode.value === 'offline' && netStatus.value?.internet !== false)),
  )

  const listEmptyText = computed(() =>
    activeTab.value === 'plugins'
      ? '暂无插件，试试导入本地 zip 或搜索 GitHub'
      : '没有匹配的技能，换个关键词或来源试试',
  )
  const listEmptyAction = computed(() =>
    activeTab.value === 'plugins' ? '导入插件 zip' : '',
  )

  const previewData = computed<LibraryItem | null>(() => {
    if (previewSummary.value && previewDetail.value) {
      return { ...previewSummary.value, ...previewDetail.value }
    }
    return previewDetail.value ?? previewSummary.value
  })
  const previewTitle = computed(() => previewData.value?.name || '条目预览')
  const previewInstalled = computed(() => itemInstalled(previewData.value))
  const previewSourceUrl = computed(() => {
    const url = previewData.value?.source_url
    return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : ''
  })
  const previewPrompt = computed(() => definitionExcerpt(definitionOf(previewData.value)))
  const previewManifest = computed(() => manifestExcerpt(previewData.value))

  function openLocalSkillManagement() {
    router.push('/evolution')
  }

  async function loadNetworkStatus() {
    netLoading.value = true
    try {
      const status = await searchApi.status()
      if (disposed) return
      netStatus.value = status
      netMode.value = status.mode ?? 'unknown'
    } catch {
      if (disposed) return
      netStatus.value = null
      netMode.value = 'unknown'
    } finally {
      if (!disposed) netLoading.value = false
    }
  }

  async function loadItems(fallbackOnError = false) {
    if (activeTab.value === 'installed') {
      await loadInstalled(true)
      return
    }
    const requestId = ++listRequestId
    if (sourceFilter.value === 'github' && !query.value.trim()) {
      items.value = []
      listError.value = ''
      listState.value = 'empty'
      listHint.value = '请输入关键词后再搜索 GitHub'
      return
    }

    listState.value = 'loading'
    listHint.value = ''
    listError.value = ''
    try {
      const res = (await libraryApi.search({
        query: query.value.trim() || undefined,
        item_type: typeFilter.value === 'all' ? undefined : typeFilter.value,
        source: sourceFilter.value === 'all' ? undefined : sourceFilter.value,
        limit: 50,
      })) as unknown as MarketSearchResponse
      if (disposed || requestId !== listRequestId) return

      const offline = res.offline === true || res.network?.mode === 'offline'
      if (offline) {
        listHint.value = '网络不可用，已切换为内置技能库'
        await loadBuiltinFallback(requestId)
        return
      }
      if (res.warning) listHint.value = res.warning

      const next = normalizeSearchItems(res)
      items.value = next
      listState.value = next.length ? 'ready' : 'empty'
    } catch (e) {
      if (disposed || requestId !== listRequestId) return
      const { code, message } = extractErrorMessage(e)
      if (
        fallbackOnError ||
        sourceFilter.value === 'github' ||
        code === 'NETWORK_UNAVAILABLE' ||
        code === 'NETWORK_ERROR'
      ) {
        listHint.value = '网络不可用，已切换为内置技能库'
        await loadBuiltinFallback(requestId)
        return
      }
      listError.value = friendlyErrorMessage(code, message)
      listState.value = classifyError(e)
    }
  }

  async function loadBuiltinFallback(requestId: number) {
    activeTab.value = 'skills'
    sourceFilter.value = 'builtin'
    typeFilter.value = 'skill'
    try {
      const res = (await libraryApi.search({
        query: '',
        item_type: 'skill',
        source: 'builtin',
        limit: 50,
      })) as unknown as MarketSearchResponse
      if (disposed || requestId !== listRequestId) return
      const next = normalizeSearchItems(res)
      items.value = next
      listState.value = next.length ? 'ready' : 'empty'
    } catch (e) {
      if (disposed || requestId !== listRequestId) return
      const { code, message } = extractErrorMessage(e)
      listError.value = friendlyErrorMessage(code, message)
      listState.value = classifyError(e)
    }
  }

  function submitSearch() {
    void loadItems()
  }

  function onSearchClear() {
    query.value = ''
    void loadItems()
  }

  function onSourceChange() {
    if (sourceFilter.value === 'github') {
      void runGithubSearch()
      return
    }
    void loadItems()
  }

  function onTypeChange() {
    void loadItems()
  }

  async function runGithubSearch() {
    if (githubLoading.value) return
    githubLoading.value = true
    sourceFilter.value = 'github'
    try {
      await loadItems(true)
    } finally {
      githubLoading.value = false
    }
  }

  function onTabClick(pane: { paneName?: string | number }) {
    const name = String(pane?.paneName ?? activeTab.value)
    if (name !== 'skills' && name !== 'plugins' && name !== 'installed') return
    activeTab.value = name
    if (name === 'installed') {
      void loadInstalled(true)
      return
    }
    query.value = ''
    listHint.value = ''
    if (name === 'skills') {
      // 回到技能库时保持当前归属视图；市场视图才需要重新检索
      if (scopeTab.value !== 'market') return
    } else {
      scopeTab.value = 'market'
    }
    sourceFilter.value = name === 'skills' ? 'builtin' : 'all'
    typeFilter.value = name === 'skills' ? 'skill' : 'plugin'
    void loadItems()
  }

  /** 技能区归属切换：市场视图恢复市场检索，其它视图由 SkillScopePanel 自行加载 */
  function onScopeChange(name: string | number) {
    if (String(name) === 'market') {
      query.value = ''
      listHint.value = ''
      sourceFilter.value = 'builtin'
      typeFilter.value = 'skill'
      void loadItems()
    }
  }

  function openWizard(kind: 'skill' | 'plugin' = 'skill') {
    if (!canGenerate.value) return
    wizardKind.value = kind
    wizardOpen.value = true
  }

  /** 生成成功：技能切到「我的」，插件没有归属列表，提示去专家审核 */
  function onSkillCreated(payload: { kind: 'skill' | 'plugin'; code: string }) {
    if (payload.kind === 'plugin') return
    activeTab.value = 'skills'
    scopeTab.value = 'mine'
    void nextTick(() => scopePanel.value?.reload())
  }

  function onEmptyAction() {
    if (activeTab.value === 'plugins') pickPluginFile()
  }

  function goSkillsTab() {
    activeTab.value = 'skills'
    scopeTab.value = 'market'
    query.value = ''
    sourceFilter.value = 'builtin'
    typeFilter.value = 'skill'
    void loadItems()
  }

  async function loadInstalled(force = false) {
    if (!force && installedLoaded.value) return
    installedState.value = 'loading'
    installedError.value = ''
    try {
      const res = await libraryApi.installed()
      if (disposed) return
      installed.value = normalizeInstalled(res)
      installedLoaded.value = true
      installedState.value = installed.value.length ? 'ready' : 'empty'
    } catch (e) {
      if (disposed) return
      const { code, message } = extractErrorMessage(e)
      installedError.value = friendlyErrorMessage(code, message)
      installedState.value = classifyError(e)
    }
  }

  function pickSkillFile() {
    skillFileInput.value?.click()
  }

  function pickPluginFile() {
    pluginFileInput.value?.click()
  }

  async function handleSkillImport(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      ElMessage.warning('技能文件仅支持 .md/.markdown/.txt')
      return
    }
    if (file.size > 256 * 1024) {
      ElMessage.warning('技能文件不能超过 256KB')
      return
    }
    skillImporting.value = true
    try {
      const result = (await libraryApi.importSkill(file)) as unknown as ImportResultLike
      ElMessage.success(importSkillMessage(result))
      installedLoaded.value = false
      activeTab.value = 'skills'
      query.value = ''
      listHint.value = ''
      sourceFilter.value = 'local'
      typeFilter.value = 'skill'
      await loadItems()
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      skillImporting.value = false
    }
  }

  async function handlePluginImport(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!/\.zip$/i.test(file.name)) {
      ElMessage.warning('插件包仅支持 .zip')
      return
    }
    if (file.size > 2 * 1024 * 1024) {
      ElMessage.warning('插件包不能超过 2MB')
      return
    }
    pluginImporting.value = true
    try {
      await libraryApi.importPlugin(file)
      ElMessage.success('插件已导入，等待专家审核')
      installedLoaded.value = false
      activeTab.value = 'plugins'
      query.value = ''
      listHint.value = ''
      sourceFilter.value = 'local'
      typeFilter.value = 'plugin'
      await loadItems()
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      pluginImporting.value = false
    }
  }

  async function installItem(item: LibraryItem) {
    if (!item.item_key || installingKey.value) return
    installingKey.value = item.item_key
    try {
      const result = (await libraryApi.install(item.item_key)) as unknown as InstallResultLike
      ElMessage.success(
        installNeedsReview(result) ? '已安装，等待专家审核' : '已安装并启用',
      )
      installedLoaded.value = false
      item.installed = true
      if (previewSummary.value?.item_key === item.item_key) {
        previewSummary.value = { ...previewSummary.value, installed: true }
      }
      if (previewDetail.value?.item_key === item.item_key) {
        previewDetail.value = { ...previewDetail.value, installed: true }
      }
      if (activeTab.value === 'installed') {
        await loadInstalled(true)
      } else {
        await loadItems()
      }
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      installingKey.value = ''
    }
  }

  function installFromCard(item: LibraryItem) {
    void installItem(item)
  }

  function openPreview(item: LibraryItem) {
    previewSummary.value = item
    previewDetail.value = null
    previewKey.value = item.item_key
    previewState.value = 'loading'
    previewError.value = ''
    drawerOpen.value = true
    void fetchPreview(item.item_key)
  }

  async function fetchPreview(itemKey: string) {
    previewState.value = 'loading'
    previewError.value = ''
    try {
      const detail = (await libraryApi.detail(itemKey)) as unknown as LibraryItem
      if (disposed) return
      previewDetail.value = detail
      previewState.value = 'ready'
    } catch (e) {
      if (disposed) return
      const { code, message } = extractErrorMessage(e)
      previewError.value = friendlyErrorMessage(code, message)
      previewState.value = classifyError(e)
    }
  }

  function retryPreview() {
    if (previewKey.value) void fetchPreview(previewKey.value)
  }

  function installPreview() {
    if (previewData.value) void installItem(previewData.value)
  }

  function openInstalledPreview(row: InstalledItem) {
    if (!row.item_key) return
    const item: LibraryItem = {
      item_key: row.item_key,
      item_type: row.type,
      source: 'local' as MarketItem['source'],
      code: row.code,
      name: row.name,
      version: row.version,
      description: '',
      risk_level: '',
      status: row.status,
      verified: false,
      installed: true,
    }
    openPreview(item)
  }

  onMounted(() => {
    void loadNetworkStatus()
    void loadItems()
  })

  onUnmounted(() => {
    disposed = true
  })
</script>

<style scoped>
  .library {
    padding: 4px;
  }
  .page-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .page-title {
    margin-bottom: 6px;
  }
  .sub {
    margin: 0;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .page-actions {
    flex-wrap: wrap;
  }
  .net-badge :deep(.el-tag__content) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .net-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ds-text-3);
  }
  .net-dot.online {
    background: var(--ds-success);
  }
  .net-dot.degraded {
    background: var(--ds-warning);
  }
  .net-dot.offline {
    background: var(--ds-danger);
  }
  .offline-alert {
    margin-bottom: 16px;
  }
  .degraded-alert {
    margin-bottom: 16px;
  }
  .offline-alert :deep(.el-alert__content) {
    width: 100%;
  }
  .offline-body {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .offline-text {
    flex: 1;
    min-width: 240px;
    line-height: 1.6;
  }
  .library-tabs {
    margin-bottom: 12px;
  }
  .library-tabs :deep(.el-tabs__header) {
    margin-bottom: 0;
  }
  .scope-tabs {
    margin-bottom: 12px;
  }
  .scope-tabs :deep(.el-tabs__header) {
    margin-bottom: 0;
  }
  .scope-tabs :deep(.el-tabs__item) {
    height: 34px;
    line-height: 34px;
    font-size: 13px;
  }
  .filter-panel {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    padding: 12px;
    margin-bottom: 12px;
  }
  .search-input {
    width: 320px;
    max-width: 100%;
  }
  .filter-select {
    width: 150px;
  }
  .filter-actions {
    display: flex;
    gap: 8px;
    margin-left: auto;
  }
  .inline-hint {
    margin-bottom: 12px;
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
    color: var(--ds-text-2);
    font-size: 13px;
    line-height: 1.6;
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
  }
  .installed-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }
  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .installed-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .installed-code {
    margin-top: 2px;
    font-size: 12px;
    color: var(--ds-text-3);
    word-break: break-all;
  }
  .preview {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .preview-desc {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-2);
  }
  .preview-section h4 {
    margin: 0 0 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .code-block {
    margin: 0;
    padding: 12px;
    max-height: 320px;
    overflow: auto;
    text-align: left;
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--ds-field);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .drawer-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .hidden-file {
    display: none;
  }
  @media (max-width: 720px) {
    .filter-actions {
      margin-left: 0;
      width: 100%;
    }
    .filter-select {
      width: calc(50% - 5px);
    }
    .search-input {
      width: 100%;
    }
  }
</style>
