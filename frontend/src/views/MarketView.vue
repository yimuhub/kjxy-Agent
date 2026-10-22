<template>
  <div class="market">
    <h2 class="page-title">
      Agent 市场
    </h2>
    <p class="sub">
      平台精选农业科技助手，一键装载到本租户。
    </p>

    <!-- 筛选区：搜索 + 分类 + 全选未装 -->
    <el-card class="filter-card" shadow="never">
      <div class="filter-bar">
        <el-input
          v-model="keyword"
          class="search"
          placeholder="搜索名称或描述"
          clearable
          :prefix-icon="Search"
        />
        <el-button
          :disabled="!uninstalledInView.length || installing"
          @click="selectAllUninstalled"
        >
          全选未装
        </el-button>
      </div>
      <el-radio-group
        v-model="domain"
        class="domain-group"
      >
        <el-radio-button
          v-for="d in DOMAIN_OPTIONS"
          :key="d"
          :value="d"
        >
          {{ d === 'all' ? '全部' : d }}
        </el-radio-button>
      </el-radio-group>
    </el-card>

    <!-- 卡片墙 -->
    <AsyncState
      :state="state"
      empty-text="平台还没有发布精选助手"
      @retry="loadCatalog"
    >
      <el-empty
        v-if="!filtered.length"
        description="没有匹配的助手，换个关键词或分类试试"
      />
      <el-checkbox-group
        v-else
        v-model="selectedCodes"
      >
        <el-row :gutter="16">
          <el-col
            v-for="a in filtered"
            :key="a.agent_code"
            :xs="24"
            :sm="12"
            :md="8"
          >
            <el-card
              class="agent-card"
              :class="{ 'is-installed': a.installed }"
            >
              <el-tag
                v-if="a.installed"
                class="installed-tag"
                size="small"
                type="success"
              >
                已装载
              </el-tag>
              <div class="agent-head">
                <el-checkbox
                  v-if="!a.installed"
                  :value="a.agent_code"
                  class="agent-check"
                >
                  <span class="agent-name">{{ a.emoji || '🤖' }} {{ a.name }}</span>
                </el-checkbox>
                <span
                  v-else
                  class="agent-name"
                >{{ a.emoji || '🤖' }} {{ a.name }}</span>
              </div>
              <div class="agent-desc">
                {{ a.description || '—' }}
              </div>
              <div class="agent-tags">
                <el-tag
                  size="small"
                  effect="plain"
                >
                  {{ a.domain }}
                </el-tag>
                <el-tag
                  size="small"
                  :type="a.risk_level === 'low' ? 'success' : 'warning'"
                >
                  {{ a.risk_level === 'low' ? '低风险' : '需平台审核' }}
                </el-tag>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-checkbox-group>
    </AsyncState>

    <!-- 底部悬浮操作条 -->
    <div class="action-bar">
      <span class="note">已选 {{ selectedCodes.length }} 个助手</span>
      <el-button
        type="primary"
        :loading="installing"
        :disabled="!selectedCodes.length || installing"
        @click="install"
      >
        装载所选（{{ selectedCodes.length }}）
      </el-button>
    </div>

    <!-- 装载失败明细 -->
    <el-dialog
      v-model="failedVisible"
      title="装载失败明细"
      width="560px"
    >
      <el-table
        :data="failedItems"
        size="small"
        max-height="280"
      >
        <el-table-column
          prop="code"
          label="助手编码"
          min-width="170"
          show-overflow-tooltip
        />
        <el-table-column
          prop="error"
          label="失败原因"
          min-width="220"
          show-overflow-tooltip
        />
      </el-table>
      <template #footer>
        <el-button
          type="primary"
          @click="failedVisible = false"
        >
          关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, onUnmounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
  import { Search } from '@element-plus/icons-vue'
  import { marketApi, MARKET_DOMAINS, type MarketAgent } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import { classifyError, type ViewState } from '@/utils/status'
  import AsyncState from '@/components/AsyncState.vue'

  type DomainFilter = 'all' | MarketAgent['domain']
  const DOMAIN_OPTIONS: DomainFilter[] = ['all', ...MARKET_DOMAINS]

  const router = useRouter()

  const catalog = ref<MarketAgent[]>([])
  const state = ref<ViewState>('loading')
  const keyword = ref('')
  const domain = ref<DomainFilter>('all')
  const selectedCodes = ref<string[]>([])
  const installing = ref(false)
  const failedVisible = ref(false)
  const failedItems = ref<{ code: string; error: string }[]>([])

  /** 组件卸载后不再回填状态（在途请求回来时丢弃） */
  let disposed = false

  const filtered = computed(() => {
    const kw = keyword.value.trim().toLowerCase()
    return catalog.value.filter(a => {
      if (domain.value !== 'all' && a.domain !== domain.value) return false
      if (!kw) return true
      return (
        a.name.toLowerCase().includes(kw) || (a.description ?? '').toLowerCase().includes(kw)
      )
    })
  })

  /** 当前筛选结果里未装载的助手（供「全选未装」） */
  const uninstalledInView = computed(() => filtered.value.filter(a => !a.installed))

  async function loadCatalog() {
    state.value = 'loading'
    try {
      const items = await marketApi.catalog()
      if (disposed) return
      catalog.value = items
      state.value = items.length ? 'ready' : 'empty'
      // 目录刷新后剔除已变成「已装载」的勾选项，避免重复提交
      const installedCodes = new Set(items.filter(i => i.installed).map(i => i.agent_code))
      selectedCodes.value = selectedCodes.value.filter(c => !installedCodes.has(c))
    } catch (e) {
      if (disposed) return
      state.value = classifyError(e)
    }
  }

  function selectAllUninstalled() {
    selectedCodes.value = Array.from(
      new Set([...selectedCodes.value, ...uninstalledInView.value.map(a => a.agent_code)]),
    )
  }

  async function install() {
    const codes = [...selectedCodes.value]
    if (!codes.length || installing.value) return
    installing.value = true
    try {
      const res = await marketApi.install(codes)
      if (disposed) return
      ElNotification({
        title: '装载完成',
        message: `成功 ${res.installed.length} · 跳过 ${res.skipped.length} · 失败 ${res.failed.length}`,
        type: res.failed.length ? 'warning' : 'success',
      })
      failedItems.value = res.failed
      failedVisible.value = res.failed.length > 0
      selectedCodes.value = []
      await loadCatalog()
      if (res.installed.length) {
        try {
          await ElMessageBox.confirm('已装载并自动发布，去专业助手页试用？', '装载成功', {
            confirmButtonText: '去试用',
            cancelButtonText: '留在本页',
            type: 'success',
          })
          router.push('/agents')
        } catch {
          /* 用户选择留在市场页 */
        }
      }
    } catch (e) {
      ElMessage.error(extractErrorMessage(e).message || '装载失败')
    } finally {
      if (!disposed) installing.value = false
    }
  }

  onMounted(() => {
    void loadCatalog()
  })
  onUnmounted(() => {
    disposed = true
  })
</script>

<style scoped>
  /* 设计系统 v2：.page-title 由全局提供，颜色一律走 --ds-* token */
  .page-title {
    margin-bottom: 6px;
  }
  .sub {
    margin: 0 0 16px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .filter-card {
    margin-bottom: 16px;
  }
  .filter-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
  }
  .search {
    max-width: 320px;
  }
  .domain-group {
    flex-wrap: wrap;
  }
  .filter-card :deep(.el-radio-group) {
    row-gap: 8px;
  }
  .agent-card {
    position: relative;
    margin-bottom: 16px;
  }
  /* 玻璃 hover 配方：边框提亮 + hover 玻璃底 + 上浮 2px，不加阴影 */
  .agent-card:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
    transform: translateY(-2px);
  }
  .agent-card.is-installed {
    opacity: 0.85;
  }
  .installed-tag {
    position: absolute;
    top: 10px;
    right: 10px;
  }
  .agent-head {
    min-height: 24px;
    padding-right: 64px;
  }
  .agent-check :deep(.el-checkbox__label) {
    white-space: normal;
  }
  .agent-name {
    font-weight: 600;
  }
  .agent-desc {
    margin: 6px 0 10px;
    font-size: 13px;
    color: var(--ds-text-2);
    min-height: 36px;
  }
  .agent-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .action-bar {
    position: sticky;
    bottom: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 8px;
    padding: 12px 16px;
    background: var(--ds-glass-strong);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
</style>
