<template>
  <div class="model-center">
    <div class="page-head">
      <h2 class="page-title">
        模型中心
      </h2>
      <div class="page-actions">
        <el-button
          size="small"
          @click="refreshAll"
        >
          刷新
        </el-button>
        <el-button
          size="small"
          type="primary"
          :loading="testing"
          @click="testChain"
        >
          测试当前链路
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="statusBanner"
      :type="statusBanner.type"
      :title="statusBanner.title"
      :description="statusBanner.desc"
      show-icon
      :closable="false"
      class="banner"
    />

    <el-card class="section">
      <template #header>
        当前生效解析
      </template>
      <el-descriptions
        :column="2"
        border
      >
        <el-descriptions-item label="模型供应商">
          {{ llm.provider || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="模型名称">
          {{ llm.model || '—' }}
        </el-descriptions-item>
        <el-descriptions-item label="是否已配置">
          <el-tag :type="llm.configured ? 'success' : 'danger'">
            {{ llm.configured ? '已配置' : '未配置' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="运行模式">
          <el-tag :type="llmModeTag">
            {{ llmModeLabel }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          label="对话会用谁"
          :span="2"
        >
          <span v-if="resolutionChat">{{ resolutionHead }}</span>
          <span
            v-else
            class="note"
          >正在解析…</span>
          <ol
            v-if="resolutionChat && resolutionChat.hops.length > 1"
            class="fallback-chain"
          >
            <li
              v-for="(hop, i) in resolutionChat.hops.slice(1)"
              :key="i"
            >
              兜底 {{ i + 1 }}：{{ hop.supplier_name }} · {{ hop.model_name }}（{{ hop.level_label }}）
            </li>
          </ol>
        </el-descriptions-item>
        <el-descriptions-item
          v-if="llm.reason"
          label="配置说明"
          :span="2"
        >
          <span class="note">{{ llm.reason }}</span>
        </el-descriptions-item>
      </el-descriptions>
      <div class="secret-note">
        <el-icon><Lock /></el-icon>
        <span>出于安全考虑，此处绝不展示密钥明文；保存绑定或供应商变更后即时生效，无需重启后端。</span>
      </div>
    </el-card>

    <el-tabs
      v-model="activeTab"
      class="center-tabs"
    >
      <el-tab-pane
        label="供应商"
        name="suppliers"
      >
        <ModelSuppliers @changed="loadSuppliers" />
      </el-tab-pane>

      <el-tab-pane
        label="官方供应商"
        name="relay"
      >
        <RelayChannels @changed="loadSuppliers" />
      </el-tab-pane>

      <el-tab-pane
        label="官方模型"
        name="official"
      >
        <OfficialModels />
      </el-tab-pane>

      <el-tab-pane
        label="全局默认与兜底"
        name="global"
      >
        <ModelBindings
          scope-type="global"
          scope-key=""
          title="全局默认模型（未单独绑定的能力都走这里）"
          hint="第一档为主用，后面是兜底顺序；主用被供应商停用或连续失败自动降级时，下一次调用自动落到下一档。"
          :suppliers="supplierOptions"
          :max-fallback="maxFallback"
          preview-purpose="declarative"
          @saved="onBindingSaved"
        />
      </el-tab-pane>

      <el-tab-pane
        label="按能力绑定"
        name="purpose"
      >
        <el-card class="section">
          <template #header>
            能力绑定（{{ capabilities.length }}）
          </template>
          <el-alert
            type="info"
            :closable="false"
            show-icon
            class="banner"
            title="给某条能力单独指定模型：优先级高于全局默认。例如让「文档生成」用长上下文模型、让「知识向量化」用便宜的嵌入模型。"
          />
          <el-table
            v-col-resize
            :data="capabilities"
            stripe
            max-height="520"
          >
            <el-table-column
              prop="label"
              label="能力"
              width="170"
            />
            <el-table-column
              prop="group"
              label="分组"
              width="110"
            />
            <el-table-column
              prop="hint"
              label="说明"
              show-overflow-tooltip
            />
            <el-table-column
              label="当前绑定"
              min-width="220"
            >
              <template #default="{ row }">
                <template v-if="bindingMap[row.code]?.length">
                  <el-tag
                    v-for="(item, i) in bindingMap[row.code]"
                    :key="item.id"
                    size="small"
                    :type="i === 0 ? 'success' : 'info'"
                    effect="plain"
                    class="bind-tag"
                  >
                    {{ i === 0 ? '主用' : `兜底${i}` }} {{ item.supplier_name }}{{ item.model_name ? ` · ${item.model_name}` : '' }}
                  </el-tag>
                </template>
                <span
                  v-else
                  class="note"
                >跟随全局默认</span>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="110"
            >
              <template #default="{ row }">
                <el-button
                  size="small"
                  text
                  type="primary"
                  @click="openBinding('purpose', row.code, `${row.label}（${row.code}）`)"
                >
                  配置
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="智能体模型"
        name="agent"
      >
        <AgentModelSettings
          :suppliers="supplierOptions"
          @changed="onBindingSaved"
        />
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="bindingDialog"
      append-to-body
      :title="`绑定模型：${bindingDialogTitle}`"
      width="720px"
    >
      <ModelBindings
        v-if="bindingDialog"
        :scope-type="bindingScopeType"
        :scope-key="bindingScopeKey"
        :title="bindingDialogTitle"
        hint="留空并保存即取消该层的单独绑定，回落到下一档。"
        :suppliers="supplierOptions"
        :max-fallback="maxFallback"
        @saved="onBindingSaved"
      />
      <template #footer>
        <el-button @click="bindingDialog = false">
          关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  /** 模型中心（v3.25）：供应商 + 全局默认与兜底 + 按能力/按助手绑定，一页配完。 */
  import { computed, onMounted, reactive, ref } from 'vue'
  import { Lock } from '@element-plus/icons-vue'
  import { ElMessage } from 'element-plus'
  import { llmApi, llmBindingApi, llmSupplierApi } from '@/api/modules'
  import type { LlmCapability, LlmBindingItem, LlmSupplier } from '@/api/modules'
  import ModelSuppliers from '@/components/settings/ModelSuppliers.vue'
  import AgentModelSettings from '@/components/settings/AgentModelSettings.vue'
  import ModelBindings from '@/components/settings/ModelBindings.vue'
  import RelayChannels from '@/components/settings/RelayChannels.vue'
  import OfficialModels from '@/components/settings/OfficialModels.vue'
  import type { LlmStatus } from '@/types/api'

  const activeTab = ref('suppliers')
  const llm = reactive<LlmStatus>({})
  const testing = ref(false)
  const suppliers = ref<LlmSupplier[]>([])
  const capabilities = ref<LlmCapability[]>([])
  /** scope_key（能力码或助手 id）→ 该层绑定条目（按主用→兜底顺序） */
  const bindingMap = reactive<Record<string, LlmBindingItem[]>>({})
  const maxFallback = ref(3)

  const resolutionChat = ref<{
    hops: { level_label: string; supplier_name: string | null; model_name: string | null; reason: string }[]
    resolved_supplier_id: string | null
  } | null>(null)

  const bindingDialog = ref(false)
  const bindingScopeType = ref<'purpose' | 'agent'>('purpose')
  const bindingScopeKey = ref('')
  const bindingDialogTitle = ref('')

  const supplierOptions = computed(() => suppliers.value)
  const resolutionHead = computed(() => {
    const head = resolutionChat.value?.hops?.[0]
    if (!head) return '当前没有可用供应商，调用会回落到环境变量配置'
    return `${head.supplier_name} · ${head.model_name}（${head.level_label}）`
  })

  const llmModeLabel = computed(() => {
    if (llm.mode === 'live') return '真实模型'
    if (llm.mode === 'mock') return '演示 / Mock'
    return '未知'
  })
  const llmModeTag = computed<'success' | 'info' | 'warning'>(() => {
    if (llm.mode === 'live') return 'success'
    if (llm.mode === 'mock') return 'info'
    return 'warning'
  })

  const statusBanner = computed(() => {
    if (llm.mode === 'mock') {
      return {
        type: 'info' as const,
        title: '当前运行在演示 / Mock 模型',
        desc: '在下方「供应商」里添加真实供应商并保存绑定后，即自动切到真实模型，无需重启。',
      }
    }
    if (llm.configured === false) {
      return {
        type: 'error' as const,
        title: '真实模型尚未配置完整',
        desc: llm.reason || '请检查供应商地址与密钥配置。',
      }
    }
    return null
  })

  async function loadStatus() {
    try {
      Object.assign(llm, await llmApi.status())
    } catch {
      /* 状态卡自行展示占位 */
    }
  }

  async function loadSuppliers() {
    try {
      suppliers.value = await llmSupplierApi.list()
    } catch {
      suppliers.value = []
    }
  }

  async function loadBindings() {
    for (const key of Object.keys(bindingMap)) delete bindingMap[key]
    try {
      const res = await llmBindingApi.list()
      res.items.forEach(item => {
        const bucket = (bindingMap[item.scope_key] ||= [])
        bucket.push(item)
      })
      Object.keys(bindingMap).forEach(key =>
        bindingMap[key].sort((a, b) => b.priority - a.priority),
      )
    } catch {
      /* 无绑定是正常态：全部走默认链 */
    }
  }

  async function loadCapabilities() {
    try {
      const res = await llmBindingApi.capabilities()
      capabilities.value = res.capabilities
      maxFallback.value = res.max_fallback
    } catch {
      capabilities.value = []
    }
  }

  async function loadResolution() {
    try {
      resolutionChat.value = await llmBindingApi.resolution({ purpose: 'declarative' })
    } catch {
      resolutionChat.value = null
    }
  }

  async function refreshAll() {
    await Promise.all([loadStatus(), loadSuppliers(), loadCapabilities(), loadBindings()])
    await loadResolution()
  }

  function openBinding(scopeType: 'purpose' | 'agent', scopeKey: string, title: string): void {
    bindingScopeType.value = scopeType
    bindingScopeKey.value = scopeKey
    bindingDialogTitle.value = title
    bindingDialog.value = true
  }

  /** 绑定保存后：列表标签、「对话会用谁」与状态卡都要同步，否则界面会停留在旧链路。 */
  async function onBindingSaved(): Promise<void> {
    await Promise.all([loadBindings(), loadStatus()])
    await loadResolution()
  }

  async function testChain() {
    testing.value = true
    try {
      const res = await llmBindingApi.test({ scope_type: 'global', scope_key: '' })
      if (res.ok) {
        ElMessage.success(`链路可用：${res.model || '未知模型'}，延迟 ${res.latency_ms ?? 0} ms`)
      } else {
        ElMessage.error(`链路不通：${res.note || '请检查供应商配置'}`)
      }
      await loadStatus()
      await loadResolution()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      testing.value = false
    }
  }

  onMounted(() => {
    void refreshAll()
  })
</script>

<style scoped>
  .model-center {
    padding-bottom: 24px;
  }
  .page-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .page-title {
    margin: 0;
    font-size: 20px;
  }
  .banner {
    margin-bottom: 12px;
  }
  .section {
    margin-bottom: 16px;
  }
  .center-tabs {
    margin-top: 8px;
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .secret-note {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    color: var(--ds-text-2);
    font-size: 13px;
  }
  .fallback-chain {
    margin: 6px 0 0;
    padding-left: 18px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .bind-tag {
    margin-right: 6px;
  }
</style>
