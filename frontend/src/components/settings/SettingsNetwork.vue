<template>
  <div class="settings-network">
    <AsyncState
      :state="state"
      empty-text="暂无搜索配置"
      @retry="loadAll"
    >
      <el-alert
        v-if="status && status.mode === 'offline'"
        class="offline-alert"
        type="error"
        :closable="false"
        show-icon
        title="当前设备无互联网连接"
        :description="OFFLINE_SEARCH_TIP"
      />
      <el-alert
        v-else-if="status && status.mode === 'degraded'"
        class="offline-alert"
        type="warning"
        :closable="false"
        show-icon
        :title="status.reason || DEGRADED_SEARCH_TIP"
        description="系统会自动尝试其余搜索源；本地 Skill 管理与离线功能不受影响。"
      />
      <el-alert
        v-else-if="!status"
        class="offline-alert"
        type="info"
        :closable="false"
        show-icon
        title="联网状态未知"
        description="暂时无法获取联网状态，可点击「测试连接」重新检测。"
      />

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <div class="header-left">
              <span>网络与搜索</span>
              <el-tag
                size="small"
                :type="statusTagType"
                effect="plain"
              >
                {{ statusLabel }}
              </el-tag>
            </div>
            <div class="page-actions">
              <el-button
                size="small"
                :loading="testing"
                @click="testConnection"
              >
                测试连接
              </el-button>
              <el-button
                size="small"
                type="primary"
                :loading="saving"
                @click="saveSearch"
              >
                保存
              </el-button>
            </div>
          </div>
        </template>

        <el-form
          label-width="130px"
          class="settings-form"
        >
          <el-form-item label="启用联网搜索">
            <el-switch v-model="search.enabled" />
            <span class="note">关闭后所有联网检索请求会被拒绝</span>
          </el-form-item>
          <el-form-item label="搜索供应商">
            <el-select
              v-model="search.provider"
              style="width: 240px"
            >
              <el-option
                label="自动（推荐）"
                value="auto"
              />
              <el-option
                label="SearXNG（自建）"
                value="searxng"
              />
              <el-option
                label="Tavily"
                value="tavily"
              />
              <el-option
                label="Brave Search"
                value="brave"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="内置搜索源">
            <div class="builtin-sources">
              <el-checkbox-group v-model="search.builtin_sources">
                <el-checkbox
                  v-for="o in BUILTIN_SOURCE_OPTIONS"
                  :key="o.value"
                  :value="o.value"
                >
                  {{ o.label }}
                </el-checkbox>
              </el-checkbox-group>
              <span class="note note-block">
                免密钥、零配置；靠前勾选的源优先尝试，失败自动顺延
              </span>
            </div>
          </el-form-item>
          <el-form-item
            v-if="search.provider === 'searxng'"
            label="SearXNG 地址"
          >
            <el-input
              v-model="search.searxng_url"
              placeholder="http://127.0.0.1:8888"
              style="max-width: 420px"
            />
            <div class="note">
              自建 SearXNG 需先在本机启动服务；公网地址需可访问
            </div>
          </el-form-item>
          <el-form-item label="每日检索上限">
            <el-input-number
              v-model="search.daily_limit"
              :min="1"
              :max="100000"
              :step="50"
            />
            <span class="note">1-100000 次/天</span>
          </el-form-item>
          <el-form-item label="超时（秒）">
            <el-input-number
              v-model="search.timeout_seconds"
              :min="2"
              :max="30"
            />
            <span class="note">2-30 秒</span>
          </el-form-item>
          <el-form-item label="Tavily API Key">
            <el-input
              v-model="tavilyKey"
              type="password"
              show-password
              placeholder="留空表示保持原有 Key"
              style="max-width: 420px"
            />
            <el-tag
              class="key-tag"
              size="small"
              :type="search.tavily_configured ? 'success' : 'info'"
            >
              {{ search.tavily_configured ? '已配置' : '未配置' }}
            </el-tag>
          </el-form-item>
          <el-form-item label="Brave API Key">
            <el-input
              v-model="braveKey"
              type="password"
              show-password
              placeholder="留空表示保持原有 Key"
              style="max-width: 420px"
            />
            <el-tag
              class="key-tag"
              size="small"
              :type="search.brave_configured ? 'success' : 'info'"
            >
              {{ search.brave_configured ? '已配置' : '未配置' }}
            </el-tag>
          </el-form-item>
        </el-form>

        <div
          v-if="status?.sources?.length"
          class="source-diagnostics"
        >
          <div class="diag-title">首选源诊断</div>
          <div
            v-for="item in status.sources"
            :key="item.code"
            class="diag-row"
          >
            <el-tag
              size="small"
              :type="item.ok ? 'success' : 'danger'"
              effect="plain"
            >
              {{ item.ok ? '可用' : '不可用' }}
            </el-tag>
            <span class="diag-label">{{ item.label }}</span>
            <span class="diag-reason">{{ item.reason }}</span>
          </div>
          <div class="diag-note">
            实际搜索会自动尝试其余已启用源；需要端到端验证请点「测试连接」。
          </div>
        </div>

        <div
          v-if="testResult"
          class="test-result"
        >
          <el-alert
            :type="testResult.ok ? 'success' : 'error'"
            :title="testResult.title"
            :description="testResult.desc"
            :closable="false"
            show-icon
          />
        </div>
      </el-card>
    </AsyncState>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    governanceApi,
    searchApi,
    type BuiltinSearchSource,
    type SearchProvider,
    type SearchStatus,
  } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { DEGRADED_SEARCH_TIP, OFFLINE_SEARCH_TIP } from './permissions'
  import { extractErrorMessage } from '@/api/client'
  import { classifyError, type ViewState } from '@/utils/status'

  const state = ref<ViewState>('loading')
  const saving = ref(false)
  const testing = ref(false)
  const status = ref<SearchStatus | null>(null)
  const tavilyKey = ref('')
  const braveKey = ref('')
  const testResult = ref<{ ok: boolean; title: string; desc: string } | null>(null)

  const BUILTIN_SOURCE_OPTIONS: { label: string; value: BuiltinSearchSource }[] = [
    { label: 'Brave 网页版', value: 'brave_web' },
    { label: '百度', value: 'baidu' },
    { label: '搜狗', value: 'sogou' },
    { label: '360 搜索', value: 'so360' },
    { label: '必应新闻', value: 'bing_news' },
    { label: '必应网页', value: 'bing' },
    { label: '谷歌新闻', value: 'google_news' },
    { label: 'DuckDuckGo', value: 'duckduckgo' },
  ]

  const PROVIDER_LABELS: Record<string, string> = {
    auto: '自动',
    brave_web: 'Brave 网页版',
    baidu: '百度',
    sogou: '搜狗',
    so360: '360 搜索',
    bing: '必应',
    duckduckgo: 'DuckDuckGo',
    searxng: 'SearXNG',
    tavily: 'Tavily',
    brave: 'Brave API',
  }

  const search = reactive({
    enabled: true,
    provider: 'auto' as SearchProvider,
    searxng_url: '',
    daily_limit: 500,
    timeout_seconds: 8,
    builtin_sources: ['brave_web', 'baidu', 'sogou', 'so360', 'bing_news', 'google_news'] as BuiltinSearchSource[],
    tavily_configured: false,
    brave_configured: false,
  })

  const statusLabel = computed(() => {
    if (!status.value) return '状态未知'
    if (status.value.mode === 'online') return '联网正常'
    if (status.value.mode === 'degraded') return '联网受限·已降级'
    return '离线（无网络）'
  })
  const statusTagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
    if (!status.value) return 'info'
    if (status.value.mode === 'online') return 'success'
    if (status.value.mode === 'degraded') return 'warning'
    return 'danger'
  })

  async function loadSettings(): Promise<void> {
    const data = await governanceApi.settings()
    Object.assign(search, data.search)
    state.value = 'ready'
  }

  async function loadStatus(): Promise<void> {
    try {
      status.value = await searchApi.status()
    } catch {
      status.value = null
    }
  }

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    try {
      await Promise.all([loadSettings(), loadStatus()])
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function saveSearch(): Promise<void> {
    saving.value = true
    try {
      const payload: Record<string, unknown> = {
        enabled: search.enabled,
        provider: search.provider,
        searxng_url: search.searxng_url,
        daily_limit: search.daily_limit,
        timeout_seconds: search.timeout_seconds,
        builtin_sources: [...search.builtin_sources],
      }
      // 留空 = 保持原 Key；后端只接受 *_api_key 字段
      if (tavilyKey.value.trim()) payload.tavily_api_key = tavilyKey.value.trim()
      if (braveKey.value.trim()) payload.brave_api_key = braveKey.value.trim()
      await governanceApi.updateSetting('search', payload)
      tavilyKey.value = ''
      braveKey.value = ''
      ElMessage.success('搜索配置已保存')
      await loadAll()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  async function testConnection(): Promise<void> {
    testing.value = true
    testResult.value = null
    try {
      const res = await searchApi.test()
      testResult.value = {
        ok: true,
        title: `「${PROVIDER_LABELS[res.provider] ?? res.provider}」连通成功`,
        desc: `返回 ${res.result_count} 条结果，耗时 ${res.latency_ms} ms。`,
      }
    } catch (e) {
      testResult.value = {
        ok: false,
        title: '联网测试失败',
        desc: extractErrorMessage(e).message || OFFLINE_SEARCH_TIP,
      }
    } finally {
      testing.value = false
      await loadStatus()
    }
  }

  onMounted(() => {
    void loadAll()
  })
</script>

<style scoped>
  .offline-alert {
    margin-bottom: 14px;
  }
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .settings-form {
    max-width: 760px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .note-block {
    display: block;
    margin-left: 0;
  }
  .builtin-sources {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
  }
  .key-tag {
    margin-left: 8px;
  }
  .test-result {
    margin-top: 12px;
  }
  .source-diagnostics {
    margin-top: 16px;
    padding-top: 12px;
    border-top: 1px solid var(--ds-border, #ebeef5);
  }
  .diag-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-2);
    margin-bottom: 8px;
  }
  .diag-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 26px;
    font-size: 12px;
  }
  .diag-label {
    width: 96px;
    color: var(--ds-text-2);
  }
  .diag-reason {
    color: var(--ds-text-3);
  }
  .diag-note {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
